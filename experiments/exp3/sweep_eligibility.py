#!/usr/bin/env python3
"""Experiment 3, step 2: sweep the eligibility thresholds and watch routing change.

No new model calls and no new spend: every quality number here was measured by the exp1
pilot and frozen into ds:capabilities. This varies POLICY fields and re-runs the routing
decision on the six compute items with dry_run. Per spec §3.4 the frontier is a policy
sweep, not a code change — each point on the curve is its own policy record.

Two knobs, not one. The first version of this sweep moved only `min_accuracy` and produced a
completely flat curve; the reason is that eligibility is a CONJUNCTION, and
`min_schema_compliance` (default 0.90) was excluding every measured arm regardless of the
accuracy floor. A single-axis sweep of a conjunction measures nothing, so both axes are varied
here and the binding constraint is reported per arm. The policy records from that confounded
first sweep stay in ds:policy — superseded, not deleted.

Modes:
  allow_flagged  an arm with no measurement passes the gate (the shipped default, so the
                 router stays usable while the matrix is sparse)
  refuse         nothing unmeasured passes (strict; what a fully audited deployment would want)
The contrast is itself a result: an unmeasured arm is not "unknown" to the router, it is
"allowed".

Three rolls of this sweep share the same 24 policy records — the policy is the thing being
swept, so a new roll of the sweep must not mint a new policy for the same thresholds. What
changes between rolls is the MEASUREMENT the gate reads:

  exp3-sweep     3 of 6 arms measured; the headline column was `n_choices_unmeasured` because
                 the gate fell through to arms it knew nothing about
  exp3-sweep-v2  5 of 6 arms measured, scores from the frozen (buggy) grader
  exp3-sweep-v3  5 of 6 arms measured, scores corrected by exp1 amendment B.1

No roll is deleted; each writes its own derived artifact.
"""
import copy, json, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

from decide import Router  # noqa: E402
from gates import _which_metric_failed  # noqa: E402
from store import Store, new_id, now  # noqa: E402

RUN = "exp3-sweep-v3"
BASE_POLICY = "v6"
ACC_FLOORS = [0.0, 0.2, 0.45, 0.8]
SCHEMA_FLOORS = [0.0, 0.5, 0.9]
MODES = ["allow_flagged", "refuse"]


def main():
    store = Store(L5)
    items = [json.loads(l) for l in
             (L5 / "experiments/exp1/battery/items_v2.jsonl").read_text().splitlines() if l.strip()]
    code_items = [it for it in items if it["prompt_class"] == "compute_via_code"]
    caps = store.records("capabilities", status="active")
    base = next(p for p in store.records("policy", status="active")
                if p["version_label"] == BASE_POLICY)
    by_variant = {m["id"]: m for m in store.records("models", status="active")}

    def acc_of(model_ref, cls):
        rows = [c for c in caps if c["model_ref"] == model_ref and c["prompt_class"] == cls]
        return rows[-1]["measured"]["accuracy"] if rows else None

    print(f"{len(code_items)} compute items, {len(caps)} capability rows, "
          f"{len(ACC_FLOORS) * len(SCHEMA_FLOORS) * len(MODES)} sweep points, $0")

    print("\nbinding constraint per measured arm (at the shipped defaults acc=0.8 / schema=0.9):")
    default_thr = {"min_termination_rate": base["measurement"]["min_termination_rate"],
                   "min_schema_compliance": base["measurement"]["min_schema_compliance"],
                   "min_accuracy": base["measurement"]["min_accuracy"]}
    for c in caps:
        if c["prompt_class"] != "compute_via_code":
            continue
        # `_which_metric_failed` returns "unknown" when no metric binds — inside the gate that
        # is a real signal (ineligible for a reason we can't name). On the printout it would
        # read as "excluded by: unknown" next to an ELIGIBLE arm, so the eligible case is
        # printed as itself.
        binds = ("— nothing binds (eligible)" if c["eligible"]
                 else _which_metric_failed(c, default_thr, "compute_via_code"))
        print(f"  {by_variant[c['model_ref']]['name']:28} acc={c['measured']['accuracy']:<7} "
              f"schema={c['measured']['schema_compliance']:<7} eligible={c['eligible']} "
              f"binding constraint: {binds}")

    results = []
    for mode in MODES:
        for acc_f in ACC_FLOORS:
            for sch_f in SCHEMA_FLOORS:
                label = f"sweep-{mode}-acc{acc_f}-sch{sch_f}"
                pol = next((p for p in store.records("policy", status="active")
                            if p.get("version_label") == label), None)
                if pol is None:
                    pol = copy.deepcopy(base)
                    pol.update({"id": new_id("pol"), "version_label": label, "created_at": now(),
                                "is_default": False, "source_ref": [base["id"]]})
                    pol["gates"]["verifiability"]["missing_measurement"] = mode
                    pol["measurement"]["min_accuracy"]["compute_via_code"] = acc_f
                    pol["measurement"]["min_schema_compliance"] = sch_f
                    store.append("policy", pol)

                router = Router(store=store, free_ram_gb=12.0, policy_label=label)
                chosen, refused, cost_sum, accs, unmeasured = [], 0, 0.0, [], 0
                for it in code_items:
                    d = router.route({"request_id": f"req_{RUN}_{label}_{it['id']}",
                                      "text": it["prompt"],
                                      "attachments": [{"type": "csv", "sha256": None, "bytes": 0}]},
                                     dry_run=True, run=RUN)
                    rec = store.get("decision_log", d["decision_ref"])
                    if rec["outcome"] == "refused":
                        refused += 1
                        continue
                    m = by_variant[rec["chosen"]["model_ref"]]
                    chosen.append(m["name"])
                    cost_sum += rec["feasible_set"][0]["expected_cost_usd"]
                    a = acc_of(m["id"], "compute_via_code")
                    if a is None:
                        unmeasured += 1
                    else:
                        accs.append(a)
                point = {"mode": mode, "min_accuracy": acc_f, "min_schema": sch_f,
                         "n_items": len(code_items), "refused": refused,
                         "chosen_counts": {k: chosen.count(k) for k in sorted(set(chosen))},
                         "mean_measured_accuracy": round(statistics.fmean(accs), 4) if accs else None,
                         "n_choices_unmeasured": unmeasured,
                         "est_cost_usd": round(cost_sum, 6), "policy_id": pol["id"]}
                results.append(point)
                print(f"  {mode:13} acc>={acc_f:<5} sch>={sch_f:<4} refused={refused}/{len(code_items)} "
                      f"mean_acc={point['mean_measured_accuracy']} unmeasured={unmeasured} "
                      f"${point['est_cost_usd']:.6f}  {point['chosen_counts']}")

    out = {"experiment": "exp3 step 2 — eligibility sweep", "run": RUN, "cost_usd": 0.0,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "source_measurements": sorted({c["measured"]["source_run"] for c in caps}),
           "n_capability_rows_active": len(caps),
           "note": "eligibility is a conjunction; single-axis sweeps are blind to the axis "
                   "that binds",
           "points": results}
    (HERE / "results" / "derived").mkdir(parents=True, exist_ok=True)
    fp = HERE / "results" / "derived" / f"sweep_{RUN}.json"
    fp.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")

    print("\nheadline reads:")
    for mode in MODES:
        best = max((r for r in results if r["mode"] == mode and r["mean_measured_accuracy"]),
                   key=lambda r: (r["mean_measured_accuracy"], -r["est_cost_usd"]), default=None)
        if best:
            print(f"  {mode}: best mean measured accuracy = {best['mean_measured_accuracy']} at "
                  f"acc>={best['min_accuracy']} schema>={best['min_schema']}, "
                  f"refused={best['refused']}/{best['n_items']}, est ${best['est_cost_usd']:.6f}, "
                  f"choices={best['chosen_counts']}")
    print(f"frozen: {fp.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
