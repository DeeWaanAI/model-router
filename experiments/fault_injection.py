#!/usr/bin/env python3
"""fault_injection.py — does the audit actually catch a wrong record, or only look like it?

Seven corruptions are injected into throwaway copies of the store. Three of them are
*semantic*: the forged record is written through the store, so its hash chain is valid — the
kind of mistake a buggy writer would make, which no hash can catch. One is a byte-level edit
that leaves the chain intact but the file wrong. The rest attack the registry, the bindings and
the privacy rule. For each, the audit must fail **on the expected check**, not merely fail.

Then the same audit runs on three unmodified copies: zero failures, so the detection above is
not a test that always cries wolf.

This experiment found a real gap while being written: the audit verified the chain but never
compared `data.jsonl` with it, so a tampered log passed. That check now exists (§6.1.5) and
mutation 7 is its regression test.

    .venv/bin/python experiments/fault_injection.py
"""
import json, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
sys.path.insert(0, str(L5 / "router"))

import connections as X  # noqa: E402
from audit import audit  # noqa: E402
from decide import Router  # noqa: E402
from store import Store, canon, new_id, now  # noqa: E402

BATTERY = L5 / "experiments/exp1/battery/items_v2.jsonl"


def fresh():
    tmp = Path(tempfile.mkdtemp(prefix="fi-"))
    shutil.copytree(L5 / "datasets", tmp / "datasets")
    return Store(tmp), tmp


def run_audit(store, run):
    code, rep = audit(store=store, run=run, needles=[], needle_labels=[], out=None)
    return code, rep


def a_decision(store, run, outcome=None):
    for d in store.records("decision_log"):
        if d.get("run") == run and (outcome is None or d.get("outcome") == outcome):
            return d
    raise SystemExit(f"no decision in run {run}")


def forged_copy(base, **changes):
    rec = json.loads(json.dumps(base))
    rec["id"] = new_id("dec")
    rec.update(changes)
    return rec


# ---------------------------------------------------------------- mutations
def m1_chosen_not_argmin():
    store, _ = fresh()
    src = next(d for d in store.records("decision_log")
               if d.get("run") == "exp3-sweep-v3" and d.get("outcome") == "planned"
               and len(d.get("feasible_set") or []) > 1)
    other = src["feasible_set"][-1]["model_ref"]
    rec = forged_copy(src, run="fi-chosen", chosen={"model_ref": other, "attempt": 1})
    store.append("decision_log", rec)
    return run_audit(store, "fi-chosen"), "§6.1.2"


def m2_gate_trace_edited():
    store, _ = fresh()
    src = next(d for d in store.records("decision_log")
               if d.get("run") == "exp3-sweep-v3" and d.get("outcome") == "planned")
    trace = json.loads(json.dumps(src["gate_trace"]))
    g = next(g for g in trace if g["survivors"])
    g["survivors"] = g["survivors"][:-1]
    rec = forged_copy(src, run="fi-trace", gate_trace=trace)
    store.append("decision_log", rec)
    return run_audit(store, "fi-trace"), "§6.1.1"


def m3_cost_inflated():
    store, _ = fresh()
    src = next(d for d in store.records("decision_log")
               if d.get("run") == "exp1-pilot-v3" and d.get("attempts"))
    att = json.loads(json.dumps(src["attempts"]))
    att[0]["actual_cost_usd"] = (att[0]["actual_cost_usd"] or 0.0) + 1.0
    rec = forged_copy(src, run="fi-cost", attempts=att)
    store.append("decision_log", rec)
    return run_audit(store, "fi-cost"), "§6.1.8"


def m4_eligible_lie():
    store, _ = fresh()
    src = next(c for c in store.records("capabilities", status="active")
               if c["prompt_class"] == "compute_via_code")
    rec = json.loads(json.dumps(src))
    rec.update(id=new_id("cap"), created_at=now(), eligible=not src["eligible"],
               status="active", superseded_by=None, prompt_class="prose_summary")
    store.append("capabilities", rec)
    # keep the scope on a real run so the other checks stay meaningful; prose_summary is not
    # replayed by any decision in it, so only the eligibility check should fire
    return run_audit(store, "exp1-pilot-v3"), "§6.1.3"


def m5_private_cloud_unwarned():
    store, _ = fresh()
    s = store
    cloud = {"id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
             "status": "active", "superseded_by": None, "source_ref": ["fi"],
             "label": "fi cloud", "connector": "openai_compatible",
             "base_url": "http://127.0.0.1:1/v1", "model": "fi-model",
             "auth": {"scheme": "none", "key_env": None},
             "locus": "cloud", "privacy_class": "private",
             "price": {"input_per_mtok": 0.1, "output_per_mtok": 0.1, "source": "user_declared"},
             "context_window": 100000, "output_budget_tokens": 512, "tokenizer_ref": None,
             "request_params": None, "timeout_s": 1.0, "local_resources": None,
             "health": None, "declared_at": now()}
    # make the forged endpoint the only candidate, otherwise the free local arm wins on price
    for m in s.records("models", status="active"):
        if "connector" in m:
            s.set_status("models", m["id"], "retired", superseded_by=None)
    s.append("models", cloud)
    r = Router(store=s, policy_label="v9")
    d = r.route({"request_id": new_id("req"),
                 "text": "My employee salary and disciplinary letter are attached; summarise."},
                dry_run=True, run="fi-seed")
    rec = s.get("decision_log", d["decision_ref"])
    if not rec.get("privacy_warning") or rec["privacy_class"] != "private":
        raise SystemExit("could not construct a private->cloud decision under v9")
    forged = forged_copy(rec, run="fi-warn", privacy_warning=None)
    s.append("decision_log", forged)
    return run_audit(s, "fi-warn"), "§6.1.7"


def m6_dangling_binding():
    store, _ = fresh()
    b = next(b for b in store.bindings("decision_log"))
    rec = json.loads(json.dumps(b))
    rec["id"] = new_id("b")
    rec["sources"] = [{"dataset": "ds:policy", "record": "pol_does_not_exist"}]
    store.append_binding("decision_log", rec)
    return run_audit(store, "exp1-pilot-v3"), "§6.1.6"


def m7_data_file_tampered():
    store, tmp = fresh()
    p = tmp / "datasets" / "decision_log" / "data.jsonl"
    lines = p.read_text().splitlines(keepends=True)
    idx = next(i for i, l in enumerate(lines) if '"outcome":"planned"' in l)
    lines[idx] = lines[idx].replace('"outcome":"planned"', '"outcome":"refused"', 1)
    p.write_text("".join(lines))
    return run_audit(store, "exp3-sweep-v3"), "§6.1.5"


MUTATIONS = [
    ("M1 chosen is not the argmin", m1_chosen_not_argmin),
    ("M2 gate trace survivor list edited", m2_gate_trace_edited),
    ("M3 actual cost inflated", m3_cost_inflated),
    ("M4 eligible contradicts its snapshot", m4_eligible_lie),
    ("M5 private->cloud without warning", m5_private_cloud_unwarned),
    ("M6 binding source does not resolve", m6_dangling_binding),
    ("M7 data.jsonl edited, chain untouched", m7_data_file_tampered),
]


def main():
    results = []
    print("=== injected faults (each must fail on its own check) ===")
    for label, fn in MUTATIONS:
        (code, rep), expected = fn()
        caught = code != 0 and any(expected in f for f in rep["failures"])
        results.append({"mutation": label, "expected_check": expected, "exit": code,
                        "failures": rep["failures"], "caught_on_expected_check": caught})
        print(f"  [{'PASS' if caught else 'FAIL'}] {label:44} exit={code} "
              f"expected={expected} failures={rep['failures'][:2]}")

    print("\n=== false positives (unmodified copies must pass) ===")
    clean = []
    for run in ("exp2-v5", "exp1-pilot-v3", "exp3-sweep-v3"):
        store, _ = fresh()
        code, rep = run_audit(store, run)
        clean.append({"run": run, "exit": code, "failures": rep["failures"]})
        print(f"  [{'PASS' if code == 0 else 'FAIL'}] clean {run:16} exit={code} "
              f"failures={rep['failures']}")

    detected = sum(1 for r in results if r["caught_on_expected_check"])
    fp = sum(1 for c in clean if c["exit"] != 0)
    out = {"experiment": "audit fault injection", "cost_usd": 0.0,
           "mutations": results, "clean_controls": clean,
           "detected_on_expected_check": f"{detected}/{len(results)}",
           "false_positives": f"{fp}/{len(clean)}",
           "note": "mutations M1-M6 are chained records, so the hash chain cannot catch them; "
                   "M7 is a byte edit that leaves the chain intact"}
    d = HERE / "results" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fault_injection.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"\n  detected {detected}/{len(results)}, false positives {fp}/{len(clean)}")
    print(f"frozen: {(d / 'fault_injection.json').relative_to(L5)}")
    return 0 if detected == len(results) and fp == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
