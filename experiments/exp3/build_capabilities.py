#!/usr/bin/env python3
"""Experiment 3, step 1: freeze measured capability rows into ds:capabilities.

The rows come from experiment 1's frozen raw runs — no new model calls, no new spend. That
matters: a capability row must trace to a frozen run with raw artifacts, and reusing exp1's
runs means the two experiments share one evidence base instead of paying twice.

Each row is (variant, prompt_class, output_budget). `eligible` is written as the cached
derivation it is specified to be (spec §1.3): computed here from `measured` + the policy
thresholds, with the thresholds COPIED into `thresholds_snapshot` so the row stays
checkable after the policy moves on. The audit recomputes it from the snapshot.

A sample too small to disqualify is recorded honestly rather than silently used: policy
requires n >= min_samples for an exclusion, so rows with n below that can support an
eligibility PASS but never a disqualification (gates.gate_verifiability applies this).
"""
import json, statistics, sys, time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

from store import Store, new_id, now  # noqa: E402
import gates as G  # noqa: E402

RECIPE = "termination_and_schema_and_accuracy_v1"
EXP1_RUN = "exp1-pilot-v3"
# Amendment B.1: the frozen roll's raw outputs, rescored with the key names the battery's own
# prompts mandated. results/raw/arms_exp1-pilot-v3.jsonl is untouched; this sidecar is what the
# registry is allowed to cite, and every row says which grader produced its numbers.
EXP1_RAW = (L5 / "experiments" / "exp1" / "results" / "derived"
            / f"arms_{EXP1_RUN}-regraded.jsonl")
GRADER = "B.1-key-alias (experiments/exp1/regrade.py)"
REGRADE_META = (L5 / "experiments" / "exp1" / "results" / "derived"
                / f"regrade_{EXP1_RUN}.json")


def main(dry=False):
    if not EXP1_RAW.exists():
        raise SystemExit(f"no corrected raw artifact at {EXP1_RAW} — run experiments/exp1/regrade.py "
                         f"on the frozen roll first")
    if not REGRADE_META.exists():
        raise SystemExit(f"no amendment record at {REGRADE_META} — run experiments/exp1/regrade.py")
    affected = set(json.loads(REGRADE_META.read_text())["affected_items"])
    rows = [json.loads(l) for l in EXP1_RAW.read_text().splitlines() if l.strip()]
    measured = [r for r in rows if r.get("status") != "excluded_context" and r.get("quality") is not None]
    skipped = [r for r in rows if r.get("status") == "excluded_context"]
    print(f"exp1 raw: {len(rows)} arm-runs, {len(measured)} measurable, "
          f"{len(skipped)} skipped-for-context")

    store = Store(L5)
    pol = next(p for p in store.records("policy", status="active") if p["version_label"] == "v6")
    thr = pol["measurement"]
    snapshot = store.registry_snapshot_hash()

    # the output budget is a property of the variant, not of the raw row
    by_variant = {m["id"]: m for m in store.records("models")}
    buckets = defaultdict(list)
    for r in measured:
        buckets[(r["model_ref"], r["prompt_class"])].append(r)

    existing = {(c["model_ref"], c["prompt_class"], c["output_budget_tokens"]): c
                for c in store.records("capabilities", status="active")}
    made, retired_rows = [], []
    for (mref, pclass), rs in sorted(buckets.items()):
        v = by_variant.get(mref)
        if v is None:
            print(f"  skip: measurement references unknown variant {mref}")
            continue
        n = len(rs)
        acc = round(statistics.fmean(r["quality"] for r in rs), 4)
        term = round(statistics.fmean(1.0 if r["terminated"] else 0.0 for r in rs), 4)
        schema = round(statistics.fmean(1.0 if r["schema_compliance"] else 0.0 for r in rs), 4)
        ran = [r.get("code_ran") for r in rs if r.get("code_ran") is not None]
        code_ran = round(statistics.fmean(1.0 if x else 0.0 for x in ran), 4) if ran else None
        snap = {"min_termination_rate": thr["min_termination_rate"],
                "min_schema_compliance": thr["min_schema_compliance"],
                "min_accuracy": {pclass: thr["min_accuracy"].get(pclass, 0.0)}}
        # A row's grader is derived from ITS rows, not from the script. Only one status means the
        # number was genuinely left on the old scorer: `not_regradable_no_reply_text`, where the
        # in-head reply was never stored. A row with no parsable stdout was re-derived and scored
        # 0 under any key names, so it is grader-independent and does not disqualify the label.
        unregraded = sorted({r["item"] for r in rs
                             if r["item"] in affected
                             and r.get("regrade_status") == "not_regradable_no_reply_text"})
        row_grader = GRADER if not unregraded else (
            f"frozen grader for {unregraded}; B.1 could not regrade them (no reply text stored)")
        measured_obj = {"accuracy": acc, "termination_rate": term, "schema_compliance": schema,
                        "code_runs_rate": code_ran, "grader": row_grader,
                        "source_run": f"{EXP1_RUN}/{EXP1_RAW.name}",
                        "item_ids": sorted(r["item"] for r in rs)}
        rec = {"id": new_id("cap"), "created_at": now(), "spec_version": "1.1",
               "status": "active", "superseded_by": None,
               "source_ref": [EXP1_RUN],
               "model_ref": mref, "prompt_class": pclass,
               "output_budget_tokens": v["output_budget_tokens"], "n": n,
               "measured": measured_obj,
               "eligibility_derivation": RECIPE, "thresholds_snapshot": snap,
               "eligible": None}
        # eligible is a DERIVED field (spec §1.3): compute it, then let the audit recompute it
        rec["eligible"] = bool(G.ELIGIBILITY_DERIVATIONS[RECIPE](measured_obj, snap, pclass))
        key = (mref, pclass, v["output_budget_tokens"])
        made.append(rec)
        verdict = "ELIGIBLE" if rec["eligible"] else "ineligible"
        too_small = n < pol["gates"]["verifiability"]["min_samples"]
        print(f"  {v['name']:32} {pclass:20} n={n} acc={acc} term={term} schema={schema} "
              f"-> {verdict}{' (n too small to disqualify)' if too_small else ''}"
              f"{' [grader: frozen, items not regradable]' if unregraded else ''}")
        if dry:
            continue
        # A re-measurement is SUCCESSION, not quarantine (spec §9.1/§9.2): the earlier row was
        # not wrong, it is older. Retiring it keeps every decision that pinned it valid.
        store.append("capabilities", rec)
        old = existing.get(key)
        if old is not None:
            store.set_status("capabilities", old["id"], "retired", superseded_by=rec["id"])
            retired_rows.append(old["id"])

    if dry:
        print(f"\nDRY RUN: {len(made)} rows would be written; nothing changed")
        return 0
    for ds in ("capabilities",):
        ok, detail = store.verify_chain(ds)
        print(f"[{'PASS' if ok else 'FAIL'}] chain {ds}: {detail}")
        if not ok:
            return 1
    meta = {"experiment": "exp3 step 1 — measured capability rows",
            "amendment": "B.1 (exp1 key-name defect; see experiments/exp1/FREEZE.md §6)",
            "grader": GRADER,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_run": EXP1_RUN, "source_artifact": str(EXP1_RAW.relative_to(L5)),
            "policy_id": pol["id"], "registry_snapshot_hash_at_write": snapshot,
            "rows_written": len(made), "rows_superseded": retired_rows,
            "recipe": RECIPE,
            "cost_usd": 0.0,
            "note": "no new model calls; rows derive from exp1's frozen raw artifacts"}
    (HERE / "results" / "raw").mkdir(parents=True, exist_ok=True)
    # Filename carries the source run: a re-measurement writes a NEW artifact instead of
    # overwriting the evidence for the run it replaced.
    fp = HERE / "results" / "raw" / f"capabilities_{EXP1_RUN}.json"
    fp.write_text(json.dumps({**meta, "rows": made}, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(made)} candidate rows; meta -> {fp.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
