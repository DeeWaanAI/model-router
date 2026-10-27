#!/usr/bin/env python3
"""baselines.py — does the policy algebra earn its complexity? Five fixed strategies, same rows.

This is a replay over frozen per-arm rows, not a new set of model calls: every cost and every
field count here was already measured by experiment 1 (and re-scored under amendment B.1). What
is new is the comparison the paper was missing — against strategies that ignore part of the
policy:

  always-local-3b        cheapest possible, no capability check
  always-local-8b        the bigger local arm
  cheapest-cloud         the lowest declared price among cloud arms
  always-70b             a fixed mid-price arm
  always-flagship        the most expensive arm
  capability-only        per class, the highest measured accuracy; cost ignored
  router                 the decisions the layer actually made (6 compute items)

Panel A is all 12 battery items; Panel B restricts every strategy to the 6 items the routed arm
actually ran, which is the only like-for-like comparison with routing.

    .venv/bin/python experiments/baselines.py
"""
import json, shutil, sys, tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
sys.path.insert(0, str(L5 / "router"))

import connections as X  # noqa: E402
from decide import Router  # noqa: E402
from store import Store, new_id  # noqa: E402

EXP1_RUN = "exp1-pilot-v3"
ARMS = L5 / "experiments" / "exp1" / "results" / "derived" / f"arms_{EXP1_RUN}-regraded.jsonl"
ROUTED = L5 / "experiments" / "exp1" / "results" / "derived" / f"routed_{EXP1_RUN}-regraded.jsonl"


def load(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def summarise(rows, label, items=None):
    rs = [r for r in rows if r.get("quality") is not None
          and (items is None or r["item"] in items)]
    if not rs:
        return None
    fc = sum(r["fields_correct"] for r in rs)
    ft = sum(r["fields_total"] for r in rs)
    cost = sum(r["cost_usd"] for r in rs)
    return {"strategy": label, "n_items": len(rs), "fields_correct": fc, "fields_total": ft,
            "accuracy": round(fc / ft, 4) if ft else None,
            "cost_usd": round(cost, 6),
            "usd_per_correct_field": round(cost / fc, 6) if fc else None,
            "models": sorted({r["model"] for r in rs})}


def main():
    store = Store(L5)
    arms = load(ARMS)
    routed = load(ROUTED)
    by_arm = defaultdict(list)
    for r in arms:
        if r.get("quality") is not None:
            by_arm[r["arm"]].append(r)

    cap = {(c["model_ref"], c["prompt_class"]): c for c in
           store.records("capabilities", status="active")}
    models = {m["id"]: m for m in store.records("models")}
    name_to_ref = {(m.get("label") or m.get("name")): m["id"] for m in models.values()}

    def arm_rows(name):
        return by_arm.get(name, [])

    # capability-only: per prompt class, the arm with the best measured accuracy (ties -> cheaper)
    best_for_class = {}
    for (mref, pcls), c in cap.items():
        if c["n"] < 3:
            continue
        cur = best_for_class.get(pcls)
        cand = (c["measured"]["accuracy"], -c["eligible"], mref)
        if cur is None or cand[:2] > cur[:2]:
            best_for_class[pcls] = cand
    cap_arm = {}
    arm_of_ref = {r["model_ref"]: r["arm"] for r in arms if r.get("model_ref")}
    for pcls, (acc, _e, mref) in best_for_class.items():
        cap_arm[pcls] = arm_of_ref.get(mref)
    print("capability-only picks per class:",
          {k: (v, round(best_for_class[k][0], 4)) for k, v in cap_arm.items()})

    fixed = [("always-local-3b", "local-3b"), ("always-local-8b", "local-8b"),
             ("cheapest-cloud", "cloud-cheap"), ("always-70b", "cloud-70b"),
             ("always-flagship", "cloud-max")]
    compute_items = sorted({r["item"] for r in routed})

    panels = {}
    for label, items in (("A_all_12_items", None), ("B_compute_6_items", set(compute_items))):
        rows = []
        for strat, arm in fixed:
            s = summarise(arm_rows(arm), strat, items)
            if s:
                rows.append(s)
        if items is None:
            # capability-only: choose per item's class, keep that arm's row for the item
            sel = []
            for r in arms:
                if r.get("quality") is None:
                    continue
                want = cap_arm.get(r["prompt_class"])
                if want and r["arm"] == want:
                    sel.append(r)
            s = summarise(sel, "capability-only")
        else:
            sel = [r for r in arms if r.get("quality") is not None and r["item"] in items
                   and cap_arm.get(r["prompt_class"]) == r["arm"]]
            s = summarise(sel, "capability-only")
        if s:
            rows.append(s)
        r = summarise(routed, "router (actual decisions)", items)
        if r:
            rows.append(r)
        panels[label] = rows
        print(f"\n{label}")
        print(f"  {'strategy':28} {'n':>3} {'accuracy':>9} {'cost':>10} {'$/correct field':>16}")
        for x in rows:
            print(f"  {x['strategy']:28} {x['n_items']:>3} {x['accuracy']:>9} "
                  f"{x['cost_usd']:>10.6f} {str(x['usd_per_correct_field']):>16}")

    # Router rows: what the layer would choose NOW, under two policies, on the same 6 items.
    # Run in a throwaway copy so the live log never carries experiment scaffolding.
    tmp = Path(tempfile.mkdtemp(prefix="baselines-"))
    shutil.copytree(L5 / "datasets", tmp / "datasets")
    tstore = Store(tmp)
    items_txt = {}
    for r in routed:
        items_txt.setdefault(r["item"], r.get("item"))
    battery = {json.loads(l)["id"]: json.loads(l) for l in
               (L5 / "experiments/exp1/battery/items_v2.jsonl").read_text().splitlines() if l.strip()}
    # capabilities attach to the MODEL, not the registry row (RC-1)
    cap_acc = {(X.capability_key(tstore, c["model_ref"]), c["prompt_class"]):
               c["measured"]["accuracy"]
               for c in tstore.records("capabilities", status="active")}

    def conn_key(ref):
        m = tstore.get("models", ref) or {}
        return X.model_key(m.get("capability_key") or m.get("model") or m.get("upstream_id"),
                           m.get("mode"))
    policy_rows = []
    for label, policy_label in (("router, default policy (v9)", "v9"),
                                ("router, before tie-break fix (v8)", "v8"),
                                ("router, accuracy floor only", "sweep-allow_flagged-acc0.8-sch0.0"),
                                ("router, strict profile (v7)", "v7")):
        r = Router(store=tstore, policy_label=policy_label)
        accs, cost, ok = [], 0.0, True
        for item in compute_items:
            d = r.route({"request_id": new_id("req"), "text": battery[item]["prompt"]},
                        dry_run=True, run=f"baseline-{policy_label}")
            if d["outcome"] == "refused":
                ok = False
                break
            ref = d["chosen"]["model_ref"]
            rec = tstore.get("decision_log", d["decision_ref"])
            accs.append(cap_acc.get((conn_key(ref), rec["task_class"]), 0.0))
            cost += rec["feasible_set"][0]["expected_cost_usd"] or 0.0
        if ok:
            row = {"strategy": label, "n_items": len(accs),
                   "accuracy": round(sum(accs) / len(accs), 4) if accs else None,
                   "cost_usd": round(cost, 6),
                   "usd_per_correct_field": None,
                   "note": "expected accuracy from capability rows, not realised answers"}
            policy_rows.append(row)
            panels["B_compute_6_items"].append(row)
            print(f"\n  {label:32} acc={row['accuracy']} ${row['cost_usd']:.6f} (dry-run)")

    out = {"experiment": "baselines over frozen exp1 rows",
           "source": str(ARMS.relative_to(L5)), "cost_usd": 0.0,
           "note": "replay of frozen per-arm rows; no model was called",
           "capability_only_picks": {k: {"arm": v, "accuracy": round(best_for_class[k][0], 4)}
                                     for k, v in cap_arm.items()},
           "router_rows": policy_rows, "panels": panels}
    fp = HERE / "results" / "derived" / "baselines.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"\nfrozen: {fp.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
