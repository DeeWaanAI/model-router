#!/usr/bin/env python3
"""Mechanism check: can a private-labelled prompt leave the device under the v3 arm?

The exp2 battery answered "no" (M2 = 0/18 in every run), but that result is bounded by the
battery, not by the design: every prompt is short enough to fit the local 8,192-token
window, and with both cost knobs at 0 the argmin always prefers the free local arm. So the
privacy gate PERMITTED a cloud locus for confidential-classified text under v3 and cost
happened to decline it.

This script removes the cost protection by making the request too long for the local window,
so only the gates decide. Same text, two policies, dry_run, $0:

  v2 (confidential -> local only)   expected: REFUSED, gate 1 removes cloud, gate 3 removes local
  v3 (confidential -> local+cloud)  expected: routed to a CLOUD variant — data leaves the device

If v3 routes to the cloud, the divergence between M1 (gate obeys its own verdict: 0
violations) and M2 (end-to-end privacy) is demonstrated as a mechanism. That does not
measure how often it happens in a population — a battery with long items would, and is
queued as battery v2 — and this script does not claim a rate.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

from decide import Router  # noqa: E402
from store import Store  # noqa: E402

RUN = "exp2-mechanism"
# Company-vocabulary only ("disciplinary", "HR complaint") so the ruleset files it as
# CONFIDENTIAL at rank 6 — but the ground truth is PRIVATE: it is one named individual's own
# employment record. This is p11's under-classification, made long enough to exceed the
# local 8,192-token window so that only the gates, not the cost objective, decide the locus.
# Deliberately free of any rank 1-5 private keyword (no medical, financial or identifier
# vocabulary), otherwise gate 1 would exclude the cloud under both policies and the
# mechanism under test would never be reached — which is exactly what the first attempt did.
FILLER = "".join(f"row_{i},attendance,{i%12},2026-0{i%9+1}-1{i%9}\n" for i in range(1400))
TEXT = ("Summarise the disciplinary case file and the HR complaint filed by the employee "
        "named Youssef Hariri, list every outcome recorded against him, and total the "
        "attendance rows below by month.\n" + FILLER)


def main():
    store = Store(L5)
    out = {"purpose": "mechanism check: private-labelled confidential-classified text under "
                      "v2 vs v3, with the cost protection removed by length",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "mode": "dry_run", "cost_usd": 0.0, "text_chars": len(TEXT),
           "ground_truth_label": "private (one named individual's health + employment record)",
           "runs": []}
    for label in ("v2", "v3-conf-cloud"):
        r = Router(store=store, free_ram_gb=12.0, policy_label=label)
        pol = r.policy()
        d = r.route({"request_id": f"req_{RUN}_{label}", "text": TEXT,
                     "attachments": [{"type": "csv", "sha256": None, "bytes": len(FILLER)}]},
                    dry_run=True, run=RUN)
        rec = store.get("decision_log", d["decision_ref"])
        chosen = store.get("models", rec["chosen"]["model_ref"]) if rec.get("chosen") else None
        row = {"policy_label": pol["version_label"], "policy_id": pol["id"],
               "allowed_loci_confidential": pol["privacy"]["allowed_loci"]["confidential"],
               "classified": rec["privacy_class"],
               "matched_rank": rec["classifier_matched_rank"],
               "task_class": rec["task_class"], "outcome": rec["outcome"],
               "refusal_reason": rec["refusal_reason"],
               "chosen": chosen["name"] if chosen else None,
               "chosen_locus": chosen["locus"] if chosen else None,
               "gate_survivors": {g["gate"]: len(g["survivors"]) for g in rec["gate_trace"]},
               "est_tokens_in_local": next((f["est_tokens_in"] for f in rec["feasible_set"]
                                            if store.get("models", f["model_ref"])["locus"] == "local"),
                                           None),
               "decision_ref": rec["id"]}
        out["runs"].append(row)
        print(f"\n=== policy {pol['version_label']} "
              f"(confidential -> {pol['privacy']['allowed_loci']['confidential']}) ===")
        print(f"  classified={row['classified']} (rule rank {row['matched_rank']}) "
              f"task={row['task_class']}")
        print(f"  gate survivors: {row['gate_survivors']}")
        print(f"  outcome={row['outcome']} refusal={row['refusal_reason']} "
              f"chosen={row['chosen']} locus={row['chosen_locus']}")
        g1 = rec["gate_trace"][0]
        row["gate1_exclusion_reasons"] = sorted({e["reason"] for e in g1["excluded"]})
        print(f"  gate 1 exclusion reasons: {row['gate1_exclusion_reasons']}")

    # ---- both keys turned, in a THROWAWAY store so the real registry is untouched ----
    # Gate 1 is a CONJUNCTION: the policy's locus map AND the model's own clearance. The two
    # runs above show that a user editing the map alone cannot expose confidential text,
    # because every cloud variant is registered with clearance=public. This case also raises
    # one cloud variant's clearance, to test that the leak needs BOTH keys — and that it then
    # really happens. The clearance change is done by succession, never by mutating a record.
    import shutil, tempfile
    from store import DATASETS, new_id, now
    tmp = Path(tempfile.mkdtemp())
    for ds in DATASETS:
        src, dst = L5 / "datasets" / ds, tmp / "datasets" / ds
        dst.mkdir(parents=True)
        for f in ("schema.json", "data.jsonl", "bindings.jsonl", "SUPERSEDED.json"):
            shutil.copy(src / f, dst / f)
        (dst / "history.jsonl").touch()
    tstore = Store(tmp)
    cloud = sorted((m for m in tstore.records("models", status="active") if m["locus"] == "cloud"),
                   key=lambda m: m["price"]["input_per_mtok"])
    target = cloud[0]
    raised = dict(target)
    raised.update({"id": new_id("mdl"), "clearance": "confidential", "created_at": now(),
                   "registered_at": now()})
    tstore.append("models", raised)
    tstore.set_status("models", target["id"], "retired", superseded_by=raised["id"])

    rk = Router(store=tstore, free_ram_gb=12.0, policy_label="v3-conf-cloud")
    dk = rk.route({"request_id": f"req_{RUN}_bothkeys", "text": TEXT,
                   "attachments": [{"type": "csv", "sha256": None, "bytes": len(FILLER)}]},
                  dry_run=True, run=RUN + "-bothkeys")
    reck = tstore.get("decision_log", dk["decision_ref"])
    chos = tstore.get("models", reck["chosen"]["model_ref"]) if reck.get("chosen") else None
    out["both_keys"] = {"store": "throwaway (the real registry is untouched)",
                        "clearance_raised_on": target["name"],
                        "classified": reck["privacy_class"], "outcome": reck["outcome"],
                        "chosen": chos["name"] if chos else None,
                        "chosen_locus": chos["locus"] if chos else None,
                        "chosen_clearance": chos["clearance"] if chos else None,
                        "gate_survivors": {g["gate"]: len(g["survivors"]) for g in reck["gate_trace"]}}
    shutil.rmtree(tmp)
    print(f"\n=== BOTH KEYS TURNED (throwaway store): confidential->cloud AND "
          f"clearance raised on {target['name']} ===")
    print(f"  outcome={out['both_keys']['outcome']} chosen={out['both_keys']['chosen']} "
          f"locus={out['both_keys']['chosen_locus']}")

    v2r, v3r = out["runs"]
    map_alone = v3r["chosen_locus"] == "cloud"
    both_keys = out["both_keys"]["chosen_locus"] == "cloud"
    out["finding"] = {
        "v2_outcome": v2r["outcome"], "v2_locus": v2r["chosen_locus"],
        "v3_outcome": v3r["outcome"], "v3_locus": v3r["chosen_locus"],
        "v3_gate1_exclusions": v3r.get("gate1_exclusion_reasons"),
        "locus_map_alone_exposes_data": map_alone,
        "both_keys_expose_data": both_keys,
        "both_classified_as": v2r["classified"],
        "conclusion": (
            "Gate 1 is a conjunction of the policy's locus map and the model's own clearance. "
            f"Editing the map alone exposed data: {map_alone}. Editing the map AND raising a "
            f"cloud model's clearance exposed data: {both_keys}. "
            + ("So the user-facing tunable the owner flagged is NOT by itself sufficient to "
               "leak confidential text; a second, registry-level change is required. That is "
               "the fail-safe property spec §2.1 claims, demonstrated rather than asserted."
               if (both_keys and not map_alone) else
               "Result differs from the expected two-key pattern — investigate before claiming "
               "the fail-safe property."),
        ),
    }
    stamp = out["at"].replace(":", "").replace("-", "")
    fp = HERE / "results" / "raw" / f"mechanism_{RUN}_{stamp}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"\nlocus map ALONE exposes data : {map_alone}   (v2 -> {v2r['outcome']}/"
          f"{v2r['chosen_locus']}; v3 -> {v3r['outcome']}/{v3r['chosen_locus']})")
    print(f"map + raised clearance       : {both_keys}   "
          f"(-> {out['both_keys']['outcome']}/{out['both_keys']['chosen_locus']}, "
          f"{out['both_keys']['chosen']})")
    print(f"gate 1 exclusions under v3   : {v3r.get('gate1_exclusion_reasons')}")
    print(f"frozen: {fp.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
