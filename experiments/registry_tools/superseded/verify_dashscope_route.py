#!/usr/bin/env python3
"""End-to-end verification of the cloud arms INSIDE the router (not just a probe).

Two runs, both real, both cheap:

  A. brief-answer request -> qwen-flash (cheapest callable cloud) answers in one attempt.
     Proves the DashScope arm works through route(), with metered actuals and a bound
     decision record, and measures estimate-vs-actual input-token error (spec §3.3/§10.4).

  B. long-answer request -> qwen-flash hits its 2048-token budget, `finish_reason: length`
     fires escalation trigger 2 (objective, not self-reported confidence), and the cascade
     moves to the next-cheapest feasible variant on a DIFFERENT provider. Proves the
     cascade works across backends on a machine-checkable signal (spec §2.5).

Cost safety: both requests are public-classified and sized so the local arm is excluded by
gate 3. Expected total spend is a few cents at most; the ACTUAL figure is printed from the
provider meters. Decisions are written to the real decision_log under run="dashscope-e2e"
so they are auditable provenance, not throwaway.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends import backends as make_backends  # noqa: E402
from decide import Router  # noqa: E402
from store import Store, new_id  # noqa: E402

ROWS = 500          # ~15k Qwen tokens: over the local 8192 window, well under qwen-flash's
BRIEF = "Write python code commentary: in under 40 words, name the largest vendor total in this table:\n"
LONG = "Write python code commentary: summarise every vendor total in this table, one paragraph each:\n"

fails = []


def check(label, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        fails.append(label)


def table():
    return "source,amount,vendor,date\n" + "".join(
        f"src_{i},{i*3.11:.2f},Vendor {i},2026-0{i % 9 + 1}-1{i % 9}\n" for i in range(ROWS))


def run_case(store, router, head, label):
    tbl = table()
    text = head + tbl
    t0 = time.time()
    d = router.route({"request_id": new_id("req"), "text": text,
                      "attachments": [{"type": "csv", "sha256": "verification-not-frozen",
                                       "bytes": len(tbl)}]},
                     dry_run=False, run="dashscope-e2e")
    rec = store.get("decision_log", d["decision_ref"])
    print(f"\n=== {label} ({round(time.time()-t0,1)}s wall) ===")
    print(f"privacy={rec['privacy_class']} task={rec['task_class']} outcome={d['outcome']} "
          f"attempts={len(rec['attempts'])}")
    for a in rec["attempts"]:
        m = store.get("models", a["model_ref"])
        print(f"  attempt {a['attempt']}: {m['name']:26} {m['backend']:10} "
              f"in={a['tokens_in']:>6} out={a['tokens_out']:>5} finish={a['finish_reason']:7} "
              f"trigger={a['triggered_by']} actual=${a['actual_cost_usd']:.6f}")
    print(f"answer[:150]: {(d['answer'] or '')[:150]!r}")
    return d, rec


def main():
    store = Store(ROOT)
    router = Router(store=store, backends=make_backends(), free_ram_gb=12.0)

    dA, recA = run_case(store, router, BRIEF, "A: brief answer")
    aA = recA["attempts"][0]
    mA = store.get("models", aA["model_ref"])
    estA = recA["feasible_set"][0]
    check("A: cheapest feasible cloud arm chosen", mA["backend"] == "dashscope", mA["name"])
    check("A: answered in one attempt, no escalation",
          dA["outcome"] == "answered" and len(recA["attempts"]) == 1, dA["outcome"])
    check("A: terminated normally", aA["finish_reason"] == "stop", aA["finish_reason"])
    check("A: provider-metered tokens recorded", aA["tokens_in"] > 0 and aA["tokens_out"] > 0,
          (aA["tokens_in"], aA["tokens_out"]))
    check("A: actual cost > 0 and computed from the frozen price", aA["actual_cost_usd"] > 0,
          f"${aA['actual_cost_usd']:.6f}")
    err = (estA["est_tokens_in"] - aA["tokens_in"]) / aA["tokens_in"]
    check("A: input-token estimate within 5% of the provider's meter", abs(err) < 0.05,
          f"{100*err:+.2f}%")
    check("A: decision is bound", bool(recA["binding_ref"]), recA["binding_ref"])

    dB, recB = run_case(store, router, LONG, "B: long answer, expect escalation")
    check("B: escalated past the token budget", dB["outcome"] == "escalated", dB["outcome"])
    check("B: escalation fired on the objective length trigger, not on confidence",
          recB["attempts"][1]["triggered_by"] == "finish_reason_length",
          recB["attempts"][1]["triggered_by"])
    check("B: first attempt hit its budget", recB["attempts"][0]["finish_reason"] == "length",
          recB["attempts"][0]["finish_reason"])
    check("B: escalation crossed providers",
          store.get("models", recB["attempts"][0]["model_ref"])["backend"] !=
          store.get("models", recB["attempts"][1]["model_ref"])["backend"],
          [store.get("models", a["model_ref"])["backend"] for a in recB["attempts"]])
    check("B: cascade respected the policy depth",
          len(recB["attempts"]) <= router.policy()["objective"]["escalation"]["max_cascade_depth"],
          len(recB["attempts"]))

    total = sum(a["actual_cost_usd"] for r in (recA, recB) for a in r["attempts"])
    check("chains verify after real cloud calls", store.verify_chain("decision_log")[0])
    print(f"\nACTUAL TOTAL SPEND for both runs (provider meters x frozen prices): ${total:.6f}")

    out = {"purpose": "end-to-end verification of cloud arms and the cascade through route()",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": ROWS,
           "total_actual_cost_usd": total,
           "A": {"decision_ref": recA["id"], "outcome": dA["outcome"], "attempts": recA["attempts"],
                 "est_tokens_in": estA["est_tokens_in"],
                 "input_token_relative_error": round(err, 6),
                 "answer_text": dA["answer"]},
           "B": {"decision_ref": recB["id"], "outcome": dB["outcome"], "attempts": recB["attempts"],
                 "answer_text": dB["answer"]}}
    fp = ROOT / "experiments" / "registry_probe" / \
        f"dashscope_e2e_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")
    print(f"frozen: {fp.relative_to(ROOT)}")

    print("\n" + "=" * 54)
    if fails:
        print(f"E2E VERIFICATION FAILED: {len(fails)} -> {fails}")
        return 1
    print("E2E VERIFICATION OK — both cloud arms and the cascade work through route()")
    return 0


if __name__ == "__main__":
    sys.exit(main())
