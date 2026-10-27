#!/usr/bin/env python3
"""Store tests: append/validate/chain/tamper. Runs against a throwaway copy of the
real schemas, so the frozen datasets are untouched. Exit 0 = all pass."""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from store import DATASETS, Store, StoreError, new_id, now, validate_binding  # noqa: E402

ROOT = HERE.parent
fails = []


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def raises(label, fn):
    try:
        fn()
    except StoreError as e:
        check(f"{label} (rejected: {str(e)[:60]}…)", True)
        return
    except Exception as e:
        check(f"{label} (wrong exception {type(e).__name__})", False)
        return
    check(label, False)


def temp_store():
    tmp = Path(tempfile.mkdtemp())
    for ds in DATASETS:
        src, dst = ROOT / "datasets" / ds, tmp / "datasets" / ds
        dst.mkdir(parents=True)
        shutil.copy(src / "schema.json", dst / "schema.json")
        for f in ("data.jsonl", "history.jsonl", "bindings.jsonl"):
            (dst / f).touch()
        (dst / "SUPERSEDED.json").write_text("[]")
    return Store(tmp), tmp


def model_rec(**over):
    r = {
        "id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
        "status": "active", "superseded_by": None, "source_ref": [],
        "label": "llama3.2:latest", "connector": "ollama",
        "base_url": "http://127.0.0.1:11434", "model": "llama3.2:latest",
        "auth": {"scheme": "none", "key_env": None},
        "locus": "local", "mode": "chat",
        "output_budget_tokens": 2048, "privacy_class": "private",
        "price": {"input_per_mtok": 0.0, "output_per_mtok": 0.0,
                  "source": "user_declared"},
        "context_window": 131072, "tokenizer_ref": "llama3.2",
        "local_resources": {"min_free_ram_gb": 4.0, "measured_gib_vram": 11.8,
                            "watts_nominal": 30.0,
                            "watts_source": "declared_assumption_not_metered",
                            "quantization": "Q4_K_M", "parameter_size": "3.2B",
                            "model_max_context": 131072, "runtime_num_ctx": 8192,
                            "measured_output_tokens_per_s": 49.5,
                            "measured_prompt_tokens_per_s": 246.4},
        "request_params": None, "health": None, "declared_at": now(),
    }
    r.update(over)
    return r


s, tmp = temp_store()

# 1. valid append round-trips and chains
rec = model_rec()
s.append("models", rec)
check("append + read back", s.get("models", rec["id"])["label"] == "llama3.2:latest")
check("chain verifies after append", s.verify_chain("models")[0])
check("history entry is chained", "entry_hash" in
      json.loads((tmp / "datasets/models/history.jsonl").read_text().splitlines()[-1]))
check("ids are prefixed and sortable", rec["id"].startswith("mdl_") and new_id("mdl") > rec["id"])

# 2. duplicate id rejected (ids immutable)
raises("duplicate id rejected", lambda: s.append("models", dict(rec)))

# 3. schema faults rejected
raises("bad enum rejected", lambda: s.append("models", model_rec(locus="ondevice")))
raises("missing field rejected",
       lambda: s.append("models", {k: v for k, v in model_rec().items() if k != "declared_at"}))
raises("wrong type rejected", lambda: s.append("models", model_rec(context_window="131072")))
raises("nested field type rejected",
       lambda: s.append("models", model_rec(price={"input_per_mtok": "0.30",
                                                   "output_per_mtok": 0.3,
                                                   "source": "user_declared"})))
raises("list<> element type rejected",
       lambda: s.append("rules", {"id": new_id("rul"), "created_at": now(), "spec_version": "1.1",
                                  "status": "active", "superseded_by": None, "source_ref": [],
                                  "rule_kind": "privacy", "default_label": "private",
                                  "rules": [{"rank": "one", "match": {"type": "keyword_any",
                                                                      "values": ["x"]},
                                             "label": "private"}],
                                  "label_set": ["private", "public"],
                                  "validated_on": {"labelled_set": "", "n": 0, "accuracy": None}}))

# 4. nullable parent skips nested checks (cloud variant has no local_resources)
cloud = model_rec(locus="cloud", connector="openai_compatible",
                  base_url="https://api.example.com/v1", privacy_class="public",
                  local_resources=None, price={"input_per_mtok": 0.88, "output_per_mtok": 0.88,
                                               "source": "user_declared"})
s.append("models", cloud)
check("nullable parent accepted", s.get("models", cloud["id"])["locus"] == "cloud")

# 5. status transition is atomic and logged
s.set_status("models", cloud["id"], "retired", superseded_by=rec["id"])
check("status transition applied", s.get("models", cloud["id"])["status"] == "retired")
check("active filter excludes retired", cloud["id"] not in [r["id"] for r in s.records("models", status="active")])
check("chain still verifies after rewrite", s.verify_chain("models")[0])

# 6. binding validation (Qafiya §7 invariants 2 and 6)
good = {"id": new_id("b"), "kind": "claim", "target": {"dataset": "ds:models", "record": rec["id"]},
        "sources": [{"dataset": "ds:models", "record": rec["id"], "field": "price"}],
        "derivation": "min(price.input_per_mtok) filter(status='active')", "value": 0.0,
        "statistic": "min", "device": "local-m1pro",
        "authoritative_artifact": "datasets/models/data.jsonl",
        "assertion": {"statement": "x", "predicate": "label_match", "expected": 0,
                      "both_sides": {"a": 0, "b": 0}}}
s.append_binding("models", good)
check("valid binding written", len(s.bindings("models")) == 1)
raises("binding with empty sources rejected",
       lambda: validate_binding({**good, "sources": []}))
raises("claim without both_sides rejected",
       lambda: validate_binding({**good, "assertion": {"statement": "x", "predicate": "p", "expected": 0}}))

# 7. quarantine is recorded and readable
s.quarantine("capabilities", "runs/run_buggy", "chain verification failed")
check("quarantine recorded", s.superseded("capabilities")[0]["artifact"] == "runs/run_buggy")

# 8. snapshot hash is stable and content-sensitive
h1 = s.registry_snapshot_hash()
s.append("models", model_rec(name="other"))
check("snapshot hash changes when the registry changes", s.registry_snapshot_hash() != h1)

# 9. TAMPER MUST BE CAUGHT — the point of the chain
hp = tmp / "datasets/models/history.jsonl"
lines = hp.read_text().splitlines()
e = json.loads(lines[1])
e["record"]["name"] = "tampered"
lines[1] = json.dumps(e, sort_keys=True, separators=(",", ":"))
hp.write_text("\n".join(lines) + "\n")
ok, detail = s.verify_chain("models")
check(f"tampered history detected ({detail[:50]})", not ok)

shutil.rmtree(tmp)
print("\n" + "=" * 50)
if fails:
    print(f"STORE TESTS FAILED: {len(fails)}")
    sys.exit(1)
print("STORE TESTS OK")
