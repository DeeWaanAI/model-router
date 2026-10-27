#!/usr/bin/env python3
"""Experiment 1 pilot v2: in-head arithmetic vs compute-via-code, same tasks, same truth.

Answers one question with money attached: when the model writes a program instead of doing
the sum itself, how much does that cost and how correct is it?

Two consequences are measured, not assumed:
  * correctness — does the model's program RUN and print the right value (a machine fact);
  * input size — a program request is a few hundred tokens; a request to compute a 30k-token
    table in the head is 100x that. If the code path is as correct, the frontier changes shape.

For *_code items the harness executes the model's program (router/code_exec.py) in a
throwaway directory holding only the battery CSVs, with a scrubbed environment, an import
allowlist and a hard timeout. That is a guardrail for this harness on this machine, NOT a
sandbox: production execution of model-written code is Layer 3's permission problem.

Forced arms are experiment controls and stay in results/raw (they are not routing
decisions). The routed arm goes through route() and is logged and bound.
"""
import hashlib, json, statistics, sys, time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))
sys.path.insert(0, str(HERE))

import cost as K  # noqa: E402
import tokens as T  # noqa: E402
def make_backends():  # noqa: E402
    raise SystemExit("experiment 1 is frozen: its harness used the per-provider backends that "
                     "RC-2 deleted. The roll, its artifacts and its numbers remain; re-running "
                     "it would need a rewrite against the connect list.")
from code_exec import extract_code, run_code  # noqa: E402
from decide import Router  # noqa: E402
from run import score, variant_for  # noqa: E402
from store import Store  # noqa: E402

RUN = "exp1-pilot-v2"          # overridable with --run so a re-run never appends to a frozen roll
CEILING_USD = 1.50
ARMS = [("local-3b", "llama3.2:latest", "chat"),
        ("local-8b", "llama3.1:8b-instruct-q4_K_M", "chat"),
        ("cloud-cheap", "qwen-flash", "chat"),
        ("cloud-70b", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "chat"),
        ("cloud-max", "Qwen/Qwen3.8-2.4T-A95B", "chat")]
ROUTED_ARCH = "code"          # the routed arm runs the production architecture only


def load_v2():
    man = json.loads((HERE / "battery" / "manifest_v2.json").read_text())
    raw = (HERE / "battery" / "items_v2.jsonl").read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["items_sha256"]:
        raise SystemExit(f"BATTERY DRIFT: frozen {man['items_sha256'][:16]}… got {got[:16]}…")
    for fn, sha in man["corpus"].items():
        p = HERE / "battery" / "corpus" / fn
        if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise SystemExit(f"corpus drift: {fn}")
    return [json.loads(l) for l in raw.decode().splitlines() if l.strip()], man


def grade(item, text):
    """*_inhead: the reply itself is the answer. *_code: the PROGRAM'S OUTPUT is the answer."""
    if item["arch"] == "inhead":
        c, t, schema_ok, _ = score(item["expected"], text)
        # the reply text IS the measurement. Without it the score cannot be recomputed from the
        # artifact — a defect that cost amendment B.1 the ability to regrade the in-head items.
        return {"fields_correct": c, "fields_total": t,
                "quality": round(c / t, 4) if t else None, "schema_compliance": schema_ok,
                "reply_text": (text or "")[:4000],
                "code_returned": False, "code_ran": None, "program_stdout": None}
    code = extract_code(text)
    res = run_code(code, item["files"], HERE / "battery" / "corpus")
    c, t, _, _ = score(item["expected"], json.dumps(res["parsed"]) if res["parsed"] is not None else "")
    return {"fields_correct": c, "fields_total": t, "quality": round(c / t, 4) if t else None,
            "schema_compliance": isinstance(res["parsed"], dict),
            "code_returned": bool(code.strip()), "code_ran": bool(res["ran"]),
            "generated_code": code[:3000],
            "program_stdout": (res.get("stdout") or "")[:300],
            "program_stderr": (res.get("stderr") or "")[:400],
            "program_wall_s": res.get("wall_s")}


def main(run_label=None):
    global RUN
    if run_label:
        RUN = run_label
    store = Store(L5)
    items, man = load_v2()
    router_pol = Router(store=store, free_ram_gb=12.0).policy()
    print(f"battery v2: {len(items)} items sha {man['items_sha256'][:12]}…  policy {router_pol['version_label']}")
    variants = {n: variant_for(store, u, m) for n, u, m in ARMS}

    est_total = 0.0
    print("\npre-flight estimate (frozen prices, output at full budget):")
    for n, m in variants.items():
        tot = 0.0
        for it in items:
            tot += (T.count(it["prompt"], m["tokenizer_ref"]) * m["price"]["input_per_mtok"]
                    + m["output_budget_tokens"] * m["price"]["output_per_mtok"]) / 1e6
        est_total += tot
        print(f"  {n:14} ${tot:.4f}")
    print(f"  TOTAL ${est_total:.4f}  (ceiling ${CEILING_USD})")
    if est_total > CEILING_USD:
        raise SystemExit("PRE-FLIGHT HALT: over ceiling, no call made.")

    be = make_backends()
    RAW = HERE / "results" / "raw"
    RAW.mkdir(parents=True, exist_ok=True)
    rows, spent, t0 = [], 0.0, time.time()
    for it in items:
        for arm, m in variants.items():
            est_in = T.count(it["prompt"], m["tokenizer_ref"])
            room = router_pol["gates"]["context_fit"]["context_margin"] * m["context_window"]
            if est_in + m["output_budget_tokens"] > room:
                rows.append({"arm": arm, "item": it["id"], "arch": it["arch"],
                             "prompt_class": it["prompt_class"], "model": m["name"],
                             "status": "excluded_context",
                             "detail": f"need={est_in + m['output_budget_tokens']} room={room:.0f}",
                             "cost_usd": 0.0, "quality": None})
                continue
            try:
                res = be[m["backend"]].complete(m, it["prompt"])
            except Exception as e:
                rows.append({"arm": arm, "item": it["id"], "arch": it["arch"],
                             "prompt_class": it["prompt_class"], "model": m["name"],
                             "status": "error", "error": str(e)[:200],
                             "cost_usd": 0.0, "quality": None})
                print(f"  {it['id']:26} {arm:12} ERROR {str(e)[:70]}")
                continue
            cu = K.actual_cost_usd(m, res["tokens_in"], res["tokens_out"])
            spent += cu
            g = grade(it, res["text"])
            row = {"arm": arm, "item": it["id"], "arch": it["arch"],
                   "prompt_class": it["prompt_class"], "task_stem": it["task_stem"],
                   "model": m["name"], "model_ref": m["id"], "backend": m["backend"],
                   "locus": m["locus"], "est_tokens_in": est_in,
                   "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                   "finish_reason": res["finish_reason"],
                   "terminated": res["finish_reason"] != "length",
                   "cost_usd": cu, "wall_clock_s": res["wall_clock_s"], "run": RUN, **g}
            rows.append(row)
            print(f"  {it['id']:26} {arm:12} q={row['quality']} ran={row['code_ran']} "
                  f"in={res['tokens_in']:>6} out={res['tokens_out']:>5} ${cu:.5f}")

    # routed arm, production architecture
    router = Router(store=store, backends=be, free_ram_gb=12.0)
    routed = []
    for it in items:
        if it["arch"] != ROUTED_ARCH:
            continue
        d = router.route({"request_id": f"req_{RUN}_{it['id']}", "text": it["prompt"],
                          "attachments": [{"type": "csv", "sha256": None, "bytes": 0}]},
                         run=RUN)
        rec = store.get("decision_log", d["decision_ref"])
        mm = store.get("models", rec["chosen"]["model_ref"]) if rec.get("chosen") else None
        att = (rec["attempts"] or [{}])[-1]
        g = grade(it, d["answer"] or "") if d["answer"] else {}
        spent += att.get("actual_cost_usd", 0.0) or 0.0
        routed.append({"item": it["id"], "arch": ROUTED_ARCH, "outcome": d["outcome"],
                       "privacy_class": rec["privacy_class"], "task_class": rec["task_class"],
                       "model": mm["name"] if mm else None, "locus": mm["locus"] if mm else None,
                       "attempts": len(rec["attempts"] or []),
                       "tokens_in": att.get("tokens_in"), "tokens_out": att.get("tokens_out"),
                       "cost_usd": att.get("actual_cost_usd", 0.0),
                       "decision_ref": rec["id"], "binding_ref": rec["binding_ref"], **g})
        print(f"  ROUTED {it['id']:26} -> {routed[-1]['model']} ({routed[-1]['locus']}) "
              f"q={routed[-1].get('quality')} ran={routed[-1].get('code_ran')} "
              f"${routed[-1]['cost_usd']:.5f}")

    (RAW / f"arms_{RUN}.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    (RAW / f"routed_{RUN}.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in routed))

    def agg(rs, key):
        out = defaultdict(list)
        for r in rs:
            if r.get("quality") is not None:
                out[r[key]].append(r)
        return {k: {"n": len(v),
                    "quality": round(statistics.fmean(x["quality"] for x in v), 4),
                    "cost_usd": round(sum(x["cost_usd"] for x in v), 6),
                    "code_ran": (round(statistics.fmean(1.0 if x.get("code_ran") else 0.0
                                                        for x in v), 4)
                                 if v[0]["arch"] == "code" else None),
                    "skipped_context": sum(1 for x in rs if x.get("status") == "excluded_context"
                                           and x[key] == k),
                    "errors": sum(1 for x in rs if x.get("status") == "error" and x[key] == k)}
                for k, v in sorted(out.items())}

    by_arch = agg(rows, "arch")
    by_arm = agg(rows, "arm")
    summary = {"by_architecture": by_arch, "by_arm": by_arm,
               "routed": {"n": len(routed),
                          "quality": round(statistics.fmean(r["quality"] for r in routed
                                                            if r.get("quality") is not None), 4)
                          if any(r.get("quality") is not None for r in routed) else None,
                          "cost_usd": round(sum(r["cost_usd"] for r in routed), 6),
                          "code_ran_rate": round(statistics.fmean(1.0 if r.get("code_ran") else 0.0
                                                                  for r in routed), 4),
                          "loci": sorted({str(r["locus"]) for r in routed}),
                          "models": sorted({str(r["model"]) for r in routed})}}
    (RAW / f"summary_{RUN}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    (RAW / f"run_meta_{RUN}.json").write_text(json.dumps({
        "run": RUN, "battery_sha256": man["items_sha256"], "n_items": len(items),
        "policy_id": router_pol["id"], "policy_label": router_pol["version_label"],
        "preflight_estimate_usd": round(est_total, 6), "ACTUAL_SPEND_USD": round(spent, 6),
        "ceiling_usd": CEILING_USD, "harness_wall_s": round(time.time() - t0, 1),
        "cost_usd": round(spent, 6),
        "note": "forced arms are controls, kept in results/raw; routed arm is logged+bound",
        "execution": "model-written programs ran under router/code_exec.py: throwaway dir, "
                     "battery CSVs only, scrubbed env, import allowlist, timeout. Guardrail, "
                     "not a sandbox.",
    }, indent=1, sort_keys=True) + "\n")

    print("\n=== by architecture (forced arms) ===")
    print(json.dumps(by_arch, indent=1, sort_keys=True))
    print("\n=== routed arm ===")
    print(json.dumps(summary["routed"], indent=1, sort_keys=True))
    print(f"\nACTUAL SPEND: ${spent:.6f} (pre-flight ${est_total:.4f}, ceiling ${CEILING_USD})")
    return 0


if __name__ == "__main__":
    lab = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else None
    sys.exit(main(lab))
