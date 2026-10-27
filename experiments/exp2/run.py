#!/usr/bin/env python3
"""Experiment 2: privacy policy compliance. $0 — every decision is dry_run (spec §5.2).

Pre-registration lives in FREEZE.md and was written before the first run; the v2 run is
pre-registered in its Amendment section, also before it ran.

This script:
  1. verifies the battery checksum against the frozen manifest and refuses to run on drift;
  2. routes all 44 prompts with dry_run=True under the given run label and policy;
  3. writes RAW results only, one file PER RUN so an earlier run's evidence is never
     overwritten (results/raw/decisions_<run>.jsonl);
  4. writes the aggregate privacy claim binding (spec §4.2);
  5. runs the replay audit and BOTH proof-of-absence scans — M7a on the prompts the
     classifier called private, M7b on the prompts LABELLED private.

Headline numbers are computed by experiments/spine.py from the frozen decision log, not
here, so two independent paths must agree.

Re-running the same label needs --force: a frozen battery must not be silently re-rolled
until it produces a nicer number, and earlier decisions stay in the log either way.
"""
import argparse, hashlib, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

from audit import audit  # noqa: E402
from decide import Router, aggregate_privacy_binding  # noqa: E402
from store import Store  # noqa: E402

BATTERY = HERE / "battery" / "prompts.jsonl"
MANIFEST = HERE / "battery" / "manifest.json"
RAW = HERE / "results" / "raw"


def load_battery():
    man = json.loads(MANIFEST.read_text())
    raw = BATTERY.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["sha256"]:
        raise SystemExit(f"BATTERY DRIFT: frozen {man['sha256'][:16]}… got {got[:16]}… — "
                         f"the pre-registration no longer describes this battery")
    rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    if len(rows) != man["n_prompts"]:
        raise SystemExit(f"battery has {len(rows)} rows, manifest says {man['n_prompts']}")
    return rows, man


def main(run, policy_label, force=False):
    store = Store(L5)
    rows, man = load_battery()
    print(f"battery verified: sha256 {man['sha256'][:16]}… n={len(rows)} "
          f"labels={json.dumps(man['by_label'], sort_keys=True)}")

    existing = [d for d in store.records("decision_log") if d.get("run") == run]
    if existing and not force:
        raise SystemExit(f"run '{run}' already has {len(existing)} decisions. Re-running would "
                         f"append a second roll of a frozen battery; pass --force only with a "
                         f"written reason. The earlier decisions stay in the log either way.")

    router = Router(store=store, free_ram_gb=12.0, policy_label=policy_label)
    pol = router.policy()
    if policy_label and pol.get("version_label") != policy_label:
        raise SystemExit(f"requested policy {policy_label} but resolved {pol.get('version_label')}")
    print(f"run={run} policy {pol['id']} ({pol['version_label']}) "
          f"logging.store_prompt_content={pol['logging']['store_prompt_content']}")
    print(f"registry snapshot {store.registry_snapshot_hash()[:16]}…; "
          f"active variants {len(store.records('models', status='active'))}; "
          f"capability rows {len(store.records('capabilities', status='active'))}")

    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"decisions_{run}.jsonl"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with out.open("w") as f:
        for row in rows:
            req = {"request_id": f"req_{run}_{row['id']}", "text": row["text"],
                   "attachments": ([{"type": row["attachment_type"], "sha256": None, "bytes": 0}]
                                   if row.get("attachment_type") else [])}
            d = router.route(req, dry_run=True, run=run)
            rec = store.get("decision_log", d["decision_ref"])
            chosen = store.get("models", rec["chosen"]["model_ref"]) if rec.get("chosen") else None
            f.write(json.dumps({
                "battery_id": row["id"], "label": row["label"],
                "adversarial": bool(row.get("adversarial")),
                "decision_ref": rec["id"], "binding_ref": rec["binding_ref"],
                "policy_ref": rec["policy_ref"],
                "classified": rec["privacy_class"],
                "classifier_matched_rank": rec["classifier_matched_rank"],
                "task_class": rec["task_class"],
                "outcome": rec["outcome"], "refusal_reason": rec["refusal_reason"],
                "chosen_model": chosen["name"] if chosen else None,
                "chosen_locus": chosen["locus"] if chosen else None,
                "chosen_backend": chosen["backend"] if chosen else None,
                "feasible_count": len(rec["feasible_set"]),
                "prompt_text_stored": rec["prompt_text"] is not None,
                "registry_snapshot_hash": rec["registry_snapshot_hash"],
            }, sort_keys=True) + "\n")
    print(f"\nwrote {out.relative_to(L5)} ({len(rows)} decisions, dry_run, $0)")

    b, stats = aggregate_privacy_binding(store, run, pol["id"])
    print(f"aggregate binding {b['id']}: both_sides={b['assertion']['both_sides']}")

    raw_rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    by_battery = {r["battery_id"]: r for r in raw_rows}
    classified_private = [(row["id"], row["text"]) for row in rows
                          if by_battery[row["id"]]["classified"] == "private"]
    labelled_private = [(row["id"], row["text"]) for row in rows if row["label"] == "private"]

    print(f"\n--- M7a proof-of-absence: {len(classified_private)} classified-private needles ---")
    code_a, rep_a = audit(store=store, run=run, needles=[t for _, t in classified_private],
                          needle_labels=[i for i, _ in classified_private])
    print(f"\n--- M7b proof-of-absence: {len(labelled_private)} labelled-private needles ---")
    code_b, rep_b = audit(store=store, run=run, needles=[t for _, t in labelled_private],
                          needle_labels=[i for i, _ in labelled_private])

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta = {
        "run": run, "started": started, "finished": finished, "mode": "dry_run",
        "cost_usd": 0.0, "battery_sha256": man["sha256"], "n_prompts": len(rows),
        "policy_id": pol["id"], "policy_label": pol["version_label"],
        "logging_store_prompt_content": pol["logging"]["store_prompt_content"],
        "registry_snapshot_hash": store.registry_snapshot_hash(),
        "aggregate_binding_id": b["id"], "aggregate_both_sides": b["assertion"]["both_sides"],
        "audit_M7a_classified_private_exit": code_a,
        "audit_M7a_failures": rep_a["failures"],
        "audit_M7b_labelled_private_exit": code_b,
        "audit_M7b_failures": rep_b["failures"],
        "audit_coverage": rep_b["coverage"],
        "n_classified_private_needles": len(classified_private),
        "n_labelled_private_needles": len(labelled_private),
    }
    (RAW / f"run_meta_{run}.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    print(f"\nM7a exit={code_a} (0=PASS)   M7b exit={code_b} (0=PASS)")
    print(f"meta: {(RAW / f'run_meta_{run}.json').relative_to(L5)}")
    print("raw written; headline numbers are computed by experiments/spine.py, not here")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="exp2")
    ap.add_argument("--policy", default=None, help="policy version_label, e.g. v1 or v2")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.run, a.policy, a.force))
