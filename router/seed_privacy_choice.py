#!/usr/bin/env python3
"""seed_privacy_choice.py — policy v8: the user decides where their data goes.

Owner decision, 2026-10-25: *"the user is responsible of their data privacy with their
providers. not ours to judge or block."* This supersedes `required_changes.md` §6's "the
privacy gate stays a hard set-filter" for the **default** policy.

What actually changes is one map and one list, both already policy data:

    privacy.allowed_loci      every class may use local AND cloud
    privacy.warn_on_cloud_for  private, confidential  -> recorded, shown, not blocked

The gate itself is untouched. It still filters, still fails closed when a class has no
mapping, and still runs before any network call — it just enforces the user's rule instead
of one this layer invented. v7 (local-only for private and confidential) stays active and is
what the mechanism proof in `test_acceptance_rc.py` check 6 pins, so the safety machinery
remains demonstrated rather than merely available.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from store import Store, new_id, now

LABEL = "v8"


def main(dry=False):
    store = Store(ROOT)
    base = next((p for p in store.records("policy", status="active")
                 if p["version_label"] == "v7"), None)
    if base is None:
        raise SystemExit("policy v7 not found — run router/apply_rc_migration.py first")
    if any(p["version_label"] == LABEL for p in store.records("policy", status="active")):
        print(f"{LABEL} already exists; nothing to do")
        return 0
    pol = json.loads(json.dumps(base))
    pol.update({"id": new_id("pol"), "version_label": LABEL, "created_at": now(),
                "is_default": True, "source_ref": [base["id"]]})
    pol["privacy"]["allowed_loci"] = {c: ["local", "cloud"] for c in
                                      ("public", "confidential", "private")}
    pol["privacy"]["warn_on_cloud_for"] = ["private", "confidential"]
    print(f"v8 from {base['id']}: allowed_loci={pol['privacy']['allowed_loci']}")
    print(f"                   warn_on_cloud_for={pol['privacy']['warn_on_cloud_for']}")
    if dry:
        print("DRY RUN: nothing written")
        return 0
    store.append("policy", pol)
    ok, detail = store.verify_chain("policy")
    print(f"[{'PASS' if ok else 'FAIL'}] chain policy: {detail}")
    print(f"v8 is the default; v7 remains active as the strict profile")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
