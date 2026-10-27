#!/usr/bin/env python3
"""apply_rc_migration.py — move the live registry to the RC-1 connect-list shape, once.

Two things happen, both as SUCCESSION rather than overwrite (spec §9.1):

1. `ds:models` gains one new connection record per existing active model, and the old
   agent-curated row is retired with `superseded_by` pointing at it. Nothing is deleted:
   historical decisions keep resolving through `registry_at()`, and capability rows keep
   their `model_ref` because `connections.capability_key()` can still walk the old record.
   Migrated prices are carried over from the pre-RC catalog; they are marked
   `source: user_declared` because from now on the list is the user's, and the user should
   confirm them (spec §14, delta D-2).
2. A new policy `v7` becomes the default: same privacy/measurement/classifier data, but
   `objective.type = min_declared_cost`, no shadow price, no latency price, no energy
   tariff, and `availability` added to the gate order (RC-3.1/3.2/4.3).

Run with `--dry` to print without writing.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import connections as X  # noqa: E402
from store import Store, new_id, now  # noqa: E402

BASES = {
    "ollama": "http://127.0.0.1:11434",
    "together": "https://api.together.xyz/v1",
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}
ENV = {"together": "TOGETHER_AI_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "ollama": None}


def connection_from(m):
    cloud = m["locus"] == "cloud"
    return {
        "id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
        "status": "active", "superseded_by": None, "source_ref": ["migration_rc1"],
        "label": m["name"],
        "connector": "ollama" if m["backend"] == "ollama" else "openai_compatible",
        "base_url": BASES[m["backend"]],
        "model": m["upstream_id"],
        "auth": ({"scheme": "bearer", "key_env": ENV[m["backend"]]} if cloud
                 else {"scheme": "none", "key_env": None}),
        "locus": m["locus"], "privacy_class": m["clearance"],
        "price": {"input_per_mtok": m["price"]["input_per_mtok"],
                  "output_per_mtok": m["price"]["output_per_mtok"],
                  "source": "user_declared"},
        "context_window": m["context_window"],
        "output_budget_tokens": m["output_budget_tokens"],
        "tokenizer_ref": m.get("tokenizer_ref"),
        "request_params": m.get("request_params"),
        "timeout_s": 60.0,
        "local_resources": m.get("local_resources"),
        "health": None,
        "declared_at": now(),
        "capability_key": m.get("upstream_id"),
    }


def policy_v7(base):
    pol = json.loads(json.dumps(base))
    pol["id"] = new_id("pol")
    pol["version_label"] = "v7"
    pol["created_at"] = now()
    pol["is_default"] = True
    pol["source_ref"] = [base["id"]]
    obj = pol["objective"]
    obj["type"] = "min_declared_cost"
    for gone in ("local_shadow_price_per_mtok", "latency_price_usd_per_s",
                 "tariff_usd_per_kwh"):
        obj.pop(gone, None)
    obj["escalation"]["triggers"] = ["call_failed", "structural_check_failed",
                                     "finish_reason_length", "confidence_below_min"]
    order = [g for g in pol["gates"]["order"] if g != "availability"]
    order.insert(order.index("privacy") + 1, "availability")
    pol["gates"]["order"] = order
    return pol


def rekey(store, dry=False):
    """Copy `mode` onto connections whose pre-RC row had one, and fix the capability key.

    Two connections can serve the same upstream model id in different modes; without the mode
    in the key, a chat measurement would be credited to the reasoning connection (found by the
    spine's coverage cross-check, not by inspection).
    """
    retired = {m["name"]: m for m in store.records("models", status="retired")
               if "connector" not in m}
    fixed = []
    for c in store.records("models", status="active"):
        if "connector" not in c:
            continue
        old = retired.get(c["label"])
        if old is None:
            continue
        want = X.model_key(c["model"], old.get("mode"))
        if c.get("mode") == old.get("mode") and c.get("capability_key") == want:
            continue
        new = json.loads(json.dumps(c))
        new.update(id=new_id("mdl"), created_at=now(), superseded_by=None,
                   source_ref=[c["id"]], mode=old.get("mode"), capability_key=want)
        fixed.append((c, new))
        print(f"  {c['label']:32} mode={old.get('mode')} key={want}")
    if dry:
        print(f"DRY RUN: {len(fixed)} connections would be rekeyed")
        return 0
    for old_c, new in fixed:
        store.append("models", new)
        store.set_status("models", old_c["id"], "retired", superseded_by=new["id"])
    print(f"rekeyed {len(fixed)} connections")
    return 0


def main(dry=False):
    store = Store(ROOT)
    old = [m for m in store.records("models", status="active") if "connector" not in m]
    print(f"{len(old)} pre-RC registry rows to migrate")
    made = []
    for m in old:
        conn = connection_from(m)
        print(f"  {m['name']:32} -> {conn['connector']:18} {conn['base_url']}")
        made.append((m, conn))
    base = next((p for p in store.records("policy", status="active")
                 if p["version_label"] == "v6"), None)
    if base is None:
        raise SystemExit("no active policy v6 to derive v7 from")
    pol = policy_v7(base)
    print(f"policy v7 from {base['id']}: objective={pol['objective']['type']} "
          f"gates={pol['gates']['order']}")
    if dry:
        rekey(store, dry=True)
        print("DRY RUN: nothing written")
        return 0
    rekey(store)
    for m, conn in made:
        store.append("models", conn)
        store.set_status("models", m["id"], "retired", superseded_by=conn["id"])
    if not any(p["version_label"] == "v7" for p in store.records("policy", status="active")):
        store.append("policy", pol)
    for ds in ("models", "policy"):
        ok, detail = store.verify_chain(ds)
        print(f"[{'PASS' if ok else 'FAIL'}] chain {ds}: {detail}")
    print(f"migrated {len(made)} connections; policy v7 is the default")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
