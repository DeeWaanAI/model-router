#!/usr/bin/env python3
"""Populate ds:models from frozen provenance artifacts.

Cloud prices are READ from the frozen Together catalog snapshot, never hardcoded
(spec §1.2: prices are records with provenance). Local facts are read from the frozen
Ollama probe. The §3.1 unit guard runs at registration: any price above the ceiling is
rejected as a suspected 1e6 error before it can enter the registry.

Arm selection (owner-confirmed 2026-10-22, spec §12.8) and the reason each is here.
Availability is MEASURED, not assumed: `probe_cloud.py` found that 6 of 8 candidates on
this account are refused as non-serverless despite carrying a catalog price, so the arms
below are the callable ones and the rest are listed as blocked.

  llama3.2:latest (local)    the small private arm; 3.2B Q4_K_M, verified by probe_local.py
  llama3.1:8b-instruct-q4_K_M (local) the middle arm the owner approved, at the SAME
                             quantization as the 3B so the comparison is size, not compression.
                             It exists because the 3B declines private medical prompts
                             outright (spec §13.4b) — a larger local arm is the difference
                             between a weaker answer and no answer
  Llama-3.3-70B-Instruct-Turbo ($1.04/Mtok) strong cloud arm; the upstream efficiency
                             experiment ran this exact model live, so frozen prior numbers
                             exist to sanity-check against
  Qwen3.8-2.4T-A95B (chat, $2.00/$6.00)    expensive arm, 1.01M-token context
  Qwen3.8-2.4T-A95B (reasoning, budget 8192) SEPARATE variant per spec §1.2 — this is the
                             model with the measured non-termination hazard (FREEZE D5, 24/24)

BLOCKED on account access, wanted but not callable serverless (measured 2026-10-22):
  meta-llama/Llama-3.2-3B-Instruct ($0.06) — would have been the cheap cloud arm,
      SIZE-MATCHED to the local 3B, isolating locus from capability
  meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo ($0.18) — would have matched a local 8B
  meta-llama/Llama-3.1-405B-Instruct ($3.50, 4096 ctx) — top of the price spread, and a
      clean demonstration of gate 3 excluding the most expensive model on cost-independent
      grounds
  Qwen/Qwen2-1.5B-Instruct ($0.02), openai/gpt-oss-20b ($0.05),
  nvidia/NVIDIA-Nemotron-Nano-9B-v2 ($0.06)
Without a cheap cloud arm the frontier's low end is missing, so the crossover can only be
located between $0 (local) and $1.04/Mtok. Reported as a limitation, not hidden.

Deliberately NOT registered: the catalog lists 98 chat endpoints priced $0. A $0 price
makes the cost objective degenerate — it wins every argmin — and free tiers carry rate
limits the catalog does not document, so a run built on them is not reproducible.
Recorded as a market property in spec §10.10, not hidden.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from store import Store, StoreError, new_id, now  # noqa: E402

UNIT = "usd_per_million_tokens"
PRICE_CEILING = 1000.0          # spec §3.1 unit guard
CATALOG = "https://api.together.xyz/v1/models"
DASHSCOPE_PRICING = "https://www.alibabacloud.com/help/en/model-studio/model-pricing"


def latest(pattern):
    hits = sorted((ROOT / "experiments").glob(pattern))
    if not hits:
        raise SystemExit(f"no frozen artifact matching {pattern} — run fetch_catalog.py / probe_local.py first")
    return hits[-1]


def catalog_prices(path):
    """Together's pricing.input/output are ALREADY USD per million tokens (spec §3.1)."""
    out = {}
    for m in json.loads(Path(path).read_text()):
        p = m.get("pricing") or {}
        def num(*keys):
            for k in keys:
                if p.get(k) not in (None, ""):
                    return float(p[k])
            return None
        out[m["id"]] = {"input": num("input", "prompt"), "output": num("output", "completion"),
                        "context_length": m.get("context_length"), "type": m.get("type")}
    return out


def variant(name, backend, upstream_id, locus, mode, reasoning_enabled, budget, clearance,
            price, context_window, tokenizer_ref, local_resources, provenance,
            request_params=None, availability=None, price_tier=None, tokenizer_agreement=None):
    if price["input_per_mtok"] > PRICE_CEILING or price["output_per_mtok"] > PRICE_CEILING:
        raise StoreError(f"unit guard (spec §3.1): {name} price {price} exceeds ${PRICE_CEILING}/Mtok — "
                         f"suspected 1e6 error; Together prices are already per million tokens")
    if locus == "cloud" and availability is None:
        raise StoreError(f"{name}: refusing to register a cloud variant with no measured "
                         f"serverless availability — run probe_cloud.py first (spec §13.2)")
    return {
        "id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
        "status": "active", "superseded_by": None, "source_ref": [],
        "name": name, "backend": backend, "upstream_id": upstream_id, "locus": locus,
        "mode": mode, "reasoning_enabled": reasoning_enabled, "output_budget_tokens": budget,
        "clearance": clearance,
        "price": {"input_per_mtok": price["input_per_mtok"], "output_per_mtok": price["output_per_mtok"],
                  "unit": UNIT, "tier": price_tier},
        "context_window": context_window, "request_params": request_params,
        "tokenizer_ref": tokenizer_ref, "tokenizer_agreement": tokenizer_agreement,
        "availability": availability,
        "local_resources": local_resources, "provenance": provenance, "registered_at": now(),
    }


# Provider quirks are registry DATA, not backend code (owner caveat (a): every tunable in
# data). Upstream FREEZE D5 records that Qwen variants must be told explicitly whether to
# think; a Llama variant sent that parameter would reject it. So each record carries the
# exact extra request body it needs, and the backends merge it verbatim.
QWEN_CHAT_PARAMS = {"reasoning": {"enabled": False}}
QWEN_THINK_PARAMS = {"reasoning": {"enabled": True}}

# Provenance is deliberately excluded from the comparison below: re-fetching the catalog
# what the router will do, so it must not mint a new record. A change in any field below
# DOES change routing, and gets a successor (spec §9.1).
COMPARE = ("name", "backend", "upstream_id", "locus", "mode", "reasoning_enabled",
           "output_budget_tokens", "clearance", "price", "context_window", "tokenizer_ref",
           "local_resources", "request_params")


def same_variant(a, b):
    """True when two records would route identically. Used to decide whether succession
    is needed (spec §9.1) — succession on a change that matters, not on a re-probe.
    `availability.probed_at` is excluded on purpose: re-measuring access on a new day
    must not mint a new record, but losing access must."""
    def sig(r):
        d = {k: r.get(k) for k in COMPARE}
        d["callable"] = (r.get("availability") or {}).get("callable")
        return json.dumps(d, sort_keys=True, default=str)
    return sig(a) == sig(b)


def main():
    store = Store(ROOT)
    cat_path = latest("catalog/together_models_*.json")
    avail_path = latest("registry_probe/cloud_availability_*.json")
    prices = catalog_prices(cat_path)
    avail_doc = json.loads(avail_path.read_text())
    avail = {r["upstream_id"]: r for r in avail_doc["results"]}
    print(f"catalog artifact : {cat_path.relative_to(ROOT)}")
    print(f"cloud probe      : {avail_path.relative_to(ROOT)} "
          f"({sum(1 for r in avail.values() if r.get('callable'))}/{len(avail)} callable)")

    cat_rel = str(cat_path.relative_to(ROOT))
    avail_rel = str(avail_path.relative_to(ROOT))
    stamp = now()

    def cloud(upstream_id, mode="chat", reasoning=False, budget=2048,
              tokenizer="tokenizer/llama3_tokenizer.json", request_params=None):
        if upstream_id not in prices:
            raise SystemExit(f"{upstream_id} not in the frozen catalog snapshot")
        p = prices[upstream_id]
        if p["input"] is None:
            raise SystemExit(f"{upstream_id} has no price in the frozen catalog")
        a = avail.get(upstream_id)
        if a is None:
            raise SystemExit(f"{upstream_id} has no availability measurement — run probe_cloud.py "
                             f"first; a catalog price is not evidence of access")
        availability = {"deployment": "serverless" if a.get("callable") else "dedicated",
                        "callable": bool(a.get("callable")), "probed_at": avail_doc.get("at"),
                        "raw_artifact": avail_rel, "error_code": a.get("error_code")}
        return variant(
            name=upstream_id.split("/")[-1] + (f"-{mode}" if mode != "chat" else ""),
            backend="together", upstream_id=upstream_id, locus="cloud", mode=mode,
            reasoning_enabled=reasoning, budget=budget, clearance="public",
            price={"input_per_mtok": p["input"], "output_per_mtok": p["output"]},
            context_window=p["context_length"], tokenizer_ref=tokenizer, local_resources=None,
            request_params=request_params, availability=availability,
            provenance={"source_url": CATALOG, "retrieved_at": stamp, "retrieved_by": "l5-agent",
                        "raw_artifact": cat_rel})

    recs = [
        cloud("meta-llama/Llama-3.2-3B-Instruct"),
        cloud("meta-llama/Llama-3.3-70B-Instruct-Turbo"),
        cloud("Qwen/Qwen3.8-2.4T-A95B", tokenizer="tokenizer/qwen25_tokenizer.json",
              request_params=QWEN_CHAT_PARAMS),
        cloud("Qwen/Qwen3.8-2.4T-A95B", mode="reasoning", reasoning=True, budget=8192,
              tokenizer="tokenizer/qwen25_tokenizer.json", request_params=QWEN_THINK_PARAMS),
        cloud("meta-llama/Llama-3.1-405B-Instruct"),
    ]

    # ---- local arms, each from its own frozen probe ----
    # min_free_ram_gb is a DECLARED floor (weights + KV cache at the registered num_ctx +
    # headroom), not a metered quantity; the supporting evidence is that the probe below
    # completed at that num_ctx on this machine. Stated as declared, per spec §10.3.
    LOCAL_ARMS = [
        ("llama3.2:latest", "registry_probe/local_llama3.2_latest_*.json", 4.0),
        ("llama3.1:8b-instruct-q4_K_M",
         "registry_probe/local_llama3.1_8b-instruct-q4_K_M_*.json", 8.0),
    ]
    for tag, pattern, min_ram in LOCAL_ARMS:
        p_path = latest(pattern)
        p = json.loads(p_path.read_text())
        p_rel = str(p_path.relative_to(ROOT))
        det, mi, pc = p["details"], p["model_info"], p["probe_call"]
        recs.append(variant(
            name=tag, backend="ollama", upstream_id=tag, locus="local",
            mode="chat", reasoning_enabled=False, budget=2048, clearance="private",
            price={"input_per_mtok": 0.0, "output_per_mtok": 0.0},
            context_window=p["requested_num_ctx"],
            tokenizer_ref="tokenizer/llama3_tokenizer.json",
            local_resources={"min_free_ram_gb": min_ram,
                             "measured_gib_vram": 11.8,
                             "watts_nominal": 30.0,
                             "watts_source": "declared_assumption_m1pro_not_metered (spec §3.2, §10.3)",
                             "quantization": det.get("quantization_level"),
                             "parameter_size": det.get("parameter_size"),
                             "model_max_context": mi.get("llama.context_length"),
                             "runtime_num_ctx": p["requested_num_ctx"],
                             "measured_output_tokens_per_s": pc.get("output_tokens_per_s"),
                             "measured_prompt_tokens_per_s": pc.get("prompt_tokens_per_s")},
            availability={"deployment": "local", "callable": True, "probed_at": p["probed_at"],
                          "raw_artifact": p_rel, "error_code": None},
            provenance={"source_url": "ollama://127.0.0.1:11434/api/show+generate",
                        "retrieved_at": stamp, "retrieved_by": "l5-agent",
                        "raw_artifact": p_rel}))
        print(f"local arm {tag}: {det.get('parameter_size')} {det.get('quantization_level')} "
              f"num_ctx={p['requested_num_ctx']} out={pc.get('output_tokens_per_s')} tok/s")

    # ---- the cheap cloud arm, on DashScope (spec §13.5 blocker 1, resolved 2026-10-22) ----
    ds_path = latest("registry_probe/dashscope_availability_*.json")
    ta_path = latest("registry_probe/tokenizer_agreement_*.json")
    ds_doc = json.loads(ds_path.read_text())
    ta_doc = json.loads(ta_path.read_text())
    flash = next((r for r in ds_doc["results"]
                  if r.get("model") == "qwen-flash" and r.get("callable")), None)
    if flash is None:
        print("qwen-flash not callable on DashScope — the cheap cloud arm stays missing")
    else:
        ta = next(c for c in ta_doc["candidates"]
                  if c["tokenizer_ref"] == "tokenizer/qwen25_tokenizer.json")
        recs.append(variant(
            name="qwen-flash", backend="dashscope", upstream_id="qwen-flash", locus="cloud",
            mode="chat", reasoning_enabled=False, budget=2048, clearance="public",
            # Prices transcribed from the published international/Singapore table on the
            # retrieval date. Tiered: 0-256K input is $0.05/$0.40; 256K-1M is $0.25/$2.00.
            # context_window is registered at the tier boundary so gate 3 keeps every
            # request inside the cheap band — the price tier becomes a routing constraint.
            price={"input_per_mtok": 0.05, "output_per_mtok": 0.40},
            price_tier="0-256K input (model max 1M; registered at the published tier boundary)",
            context_window=262144, tokenizer_ref="tokenizer/qwen25_tokenizer.json",
            local_resources=None, request_params=None,
            tokenizer_agreement={"my_tokens": ta["my_tokens"],
                                 "provider_delta_tokens": ta_doc["provider_delta_tokens"],
                                 "relative_error": ta["relative_error"],
                                 "raw_artifact": str(ta_path.relative_to(ROOT))},
            availability={"deployment": "serverless", "callable": True, "probed_at": ds_doc["at"],
                          "raw_artifact": str(ds_path.relative_to(ROOT)), "error_code": None},
            provenance={"source_url": DASHSCOPE_PRICING, "retrieved_at": stamp,
                        "retrieved_by": "l5-agent (transcribed from the published price table)",
                        "raw_artifact": str(ds_path.relative_to(ROOT))}))

    by_key = {(r["upstream_id"], r["mode"], r["output_budget_tokens"]): r
              for r in store.records("models", status="active")}
    added = retired = unchanged = uncallable = 0
    for r in recs:
        if r["locus"] == "cloud" and not r["availability"]["callable"]:
            uncallable += 1
            print(f"NOT CALLABLE (measured): {r['upstream_id']} {r['mode']} — "
                  f"{r['availability']['error_code']}; not registered")
            continue
        key = (r["upstream_id"], r["mode"], r["output_budget_tokens"])
        old = by_key.get(key)
        if old is not None and same_variant(old, r):
            unchanged += 1
            print(f"unchanged: {r['name']} {r['mode']} budget={r['output_budget_tokens']} ({old['id']})")
            continue
        store.append("models", r)
        added += 1
        print(f"registered {r['id']}  {r['locus']:5} {r['mode']:9} "
              f"in=${r['price']['input_per_mtok']}/Mtok out=${r['price']['output_per_mtok']}/Mtok "
              f"ctx={r['context_window']:>7} budget={r['output_budget_tokens']:>5}  {r['upstream_id']}")
        if old is not None:
            # Succession, not quarantine (spec §9.1): decisions that pinned the old record
            # stay valid and explainable; nothing is deleted.
            store.set_status("models", old["id"], "retired", superseded_by=r["id"])
            retired += 1
            print(f"  superseded {old['id']} -> {r['id']} (registry record changed)")

    # Any active cloud record without a measured callable=true must leave the registry:
    # the router must never select a variant it cannot call.
    for a in store.records("models", status="active"):
        if a["locus"] == "cloud" and not (a.get("availability") or {}).get("callable"):
            store.set_status("models", a["id"], "retired", superseded_by=None)
            retired += 1
            print(f"retired {a['id']} {a['upstream_id']} — no measured serverless availability")

    print(f"\n{added} new, {retired} superseded/retired, {unchanged} unchanged, "
          f"{uncallable} not callable; snapshot hash {store.registry_snapshot_hash()[:16]}…")
    for ds in ("models",):
        ok, detail = store.verify_chain(ds)
        print(f"[{'PASS' if ok else 'FAIL'}] chain {ds}: {detail}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
