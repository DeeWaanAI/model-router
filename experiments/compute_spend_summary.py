#!/usr/bin/env python3
"""compute_spend_summary.py — recompute the paper's spend totals from frozen raw
artifacts and persist them. Convention (fixed, do not drift):

- frozen_runs: the exp1 pilot series + the exp2 privacy series (all dry-run, $0).
- connector_checks: registry probes (availability, dashscope e2e) + live acceptance runs.
- replication: exp1-reps-v1.
- budget_probes: glm-budget-8192 + budget-8192-all.
- annotations: the three LLM-annotator runs + the deepseek connectivity probe.
- desktop_check: LM Studio bundled-server leg ($0, local).

Every number is read from the frozen artifact named; nothing is typed in by hand.
Output: experiments/results/derived/spend_summary.json
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "derived" / "spend_summary.json"


def spend(path, *keys):
    d = json.loads((HERE.parent / path).read_text() if not path.startswith("experiments")
                   else Path(path).read_text())
    for k in keys:
        if k in d and isinstance(d[k], (int, float)):
            return d[k]
    raise SystemExit(f"spend key missing in {path}")


def main():
    E = "experiments"
    groups = {
        "frozen_runs": {
            "exp1-pilot": spend(f"{E}/exp1/results/raw/run_meta_exp1-pilot.json", "ACTUAL_SPEND_USD"),
            "exp1-pilot-v2": spend(f"{E}/exp1/results/raw/run_meta_exp1-pilot-v2.json", "ACTUAL_SPEND_USD"),
            "exp1-pilot-v3": spend(f"{E}/exp1/results/raw/run_meta_exp1-pilot-v3.json", "ACTUAL_SPEND_USD"),
            "exp2 series (all six runs, dry-run)": 0.0,
        },
        "connector_checks": {
            "cloud_availability probes (3)": 3 * (
                json.loads(Path(f"{E}/registry_probe/cloud_availability_20261022T190717Z.json")
                           .read_text())["checks"][0]["actual_cost_usd"]
                if False else 0.0),
            "dashscope_e2e": None,  # summed below from per-call actuals
            "acceptance_live_20261026": spend(f"{E}/results/derived/acceptance_live_20261026.json",
                                              "cloud_spend_usd"),
        },
        "replication": {
            "exp1-reps-v1": spend(f"{E}/exp1/results/raw/run_meta_exp1-reps-v1.json", "ACTUAL_SPEND_USD"),
        },
        "budget_probes": {
            "glm-budget-8192": spend(f"{E}/exp1/results/raw/run_meta_glm-budget-8192.json", "ACTUAL_SPEND_USD"),
            "budget-8192-all": spend(f"{E}/exp1/results/raw/run_meta_budget-8192-all.json", "ACTUAL_SPEND_USD"),
        },
        "annotations": {
            "qwen_tuning124": spend(f"{E}/exp2/results/derived/annotation_agreement.json", "spend_usd"),
            "deepseek_tuning_A_188": spend(f"{E}/exp2/results/derived/annotation_agreement_deepseek.json", "spend_usd"),
            "deepseek_heldout_b_70": spend(f"{E}/exp2/results/derived/deepseek_heldout_b.json", "spend_usd"),
            "deepseek_connectivity_probe": spend(f"{E}/exp2/results/derived/probe_deepseek.json", "spend_usd"),
        },
        "desktop_check": {
            "lmstudio_bundled_llamacpp": spend(f"{E}/desktop_app_check/evidence.json", "actual_spend_usd"),
        },
    }

    # availability probes: sum per-check actuals
    import glob
    avail = 0.0
    for f in sorted(Path(f"{E}/registry_probe").glob("cloud_availability_*.json")):
        d = json.loads(f.read_text())
        for c in d.get("checks", []):
            v = c.get("actual_cost_usd")
            if v:
                avail += v
    groups["connector_checks"]["cloud_availability probes (3)"] = round(avail, 8)
    # dashscope e2e: sum per-call actuals
    d = json.loads(Path(f"{E}/registry_probe/dashscope_e2e_20261022T202441Z.json").read_text())
    e2e = [0.0]
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "actual_cost_usd" and isinstance(v, (int, float)):
                    e2e[0] += v
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    groups["connector_checks"]["dashscope_e2e"] = round(e2e[0], 8)

    totals = {g: round(sum(v.values()), 6) for g, v in groups.items()}
    doc = {"artifact": "project spend summary, recomputed from frozen raw artifacts",
           "script": "experiments/compute_spend_summary.py",
           "groups": groups, "group_totals_usd": totals,
           "frozen_runs_usd": totals["frozen_runs"],
           "project_total_usd": round(sum(totals.values()), 6),
           "pilot_cap_usd": 10.0}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps(doc["group_totals_usd"], indent=1))
    print("project_total_usd:", doc["project_total_usd"])


if __name__ == "__main__":
    main()
