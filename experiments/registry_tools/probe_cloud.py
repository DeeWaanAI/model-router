#!/usr/bin/env python3
"""Measure which cloud variants are ACTUALLY callable serverless, one minimal call each.

Why this exists: the 2026-10-22 catalog snapshot lists all 176 chat models with
pricing.hourly == 0, and meta-llama/Llama-3.2-3B-Instruct is priced at $0.06/Mtok — yet
the API refused it with `model_not_available: Unable to access non-serverless model`.
A catalog price is therefore NOT evidence of access. The router must not select a variant
it cannot call, so callability is measured and recorded as registry data.

Cost: one call per candidate with max_tokens=8 and a 6-word prompt. At the catalog's most
expensive price ($3.50/$3.50 per Mtok) that is under $0.0001 per candidate. The script
computes and prints the ACTUAL total from the provider's own meter.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.together import TogetherBackend  # noqa: E402
from store import Store  # noqa: E402

PROMPT = "Reply with one word: ready"
PROBE_BUDGET = 8

# Arm candidates, cheapest to most expensive. Selection rationale lives in
# register_models.py; this list only decides what gets an availability measurement.
CANDIDATES = [
    "Qwen/Qwen2-1.5B-Instruct",
    "openai/gpt-oss-20b",
    "nvidia/NVIDIA-Nemotron-Nano-9B-v2",
    "meta-llama/Llama-3.2-3B-Instruct",
    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "Qwen/Qwen3.8-2.4T-A95B",
    "meta-llama/Llama-3.1-405B-Instruct",
]


def main():
    store = Store(ROOT)
    prices = {}
    cat = sorted((ROOT / "experiments" / "catalog").glob("together_models_*.json"))[-1]
    for m in json.loads(cat.read_text()):
        p = m.get("pricing") or {}
        try:
            prices[m["id"]] = (float(p.get("input") or 0), float(p.get("output") or 0),
                               m.get("context_length"))
        except (TypeError, ValueError):
            pass

    be = TogetherBackend()
    results, total = [], 0.0
    for i, mid in enumerate(CANDIDATES):
        pin, pout, ctx = prices.get(mid, (None, None, None))
        probe_model = {"id": f"probe_{i}", "upstream_id": mid, "locus": "cloud",
                       "output_budget_tokens": PROBE_BUDGET,
                       "price": {"input_per_mtok": pin or 0.0, "output_per_mtok": pout or 0.0,
                                 "unit": "usd_per_million_tokens"},
                       "request_params": ({"reasoning": {"enabled": False}}
                                          if mid.startswith("Qwen/Qwen3") else None)}
        t0 = time.time()
        try:
            res = be.complete(probe_model, PROMPT)
            cost = (res["tokens_in"] * (pin or 0) + res["tokens_out"] * (pout or 0)) / 1e6
            total += cost
            results.append({"upstream_id": mid, "callable": True, "http_status": res["http_status"],
                            "finish_reason": res["finish_reason"], "tokens_in": res["tokens_in"],
                            "tokens_out": res["tokens_out"], "wall_clock_s": round(time.time() - t0, 3),
                            "cost_usd": cost, "response_text": res["text"],
                            "input_per_mtok": pin, "output_per_mtok": pout, "context_length": ctx})
            print(f"  OK   {mid:46} ${cost:.8f} {res['wall_clock_s']:.2f}s "
                  f"in={res['tokens_in']} out={res['tokens_out']} {res['text'][:24]!r}")
        except Exception as e:
            msg = str(e)
            code = "model_not_available" if "non-serverless" in msg or "model_not_available" in msg \
                else type(e).__name__
            results.append({"upstream_id": mid, "callable": False, "error_code": code,
                            "error": msg[:300], "input_per_mtok": pin, "output_per_mtok": pout,
                            "context_length": ctx})
            print(f"  FAIL {mid:46} {code}")

    out = {"purpose": "cloud serverless-availability probe (one minimal call per candidate)",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "prompt": PROMPT, "max_tokens": PROBE_BUDGET,
           "catalog_artifact": str(cat.relative_to(ROOT)),
           "total_actual_cost_usd": total, "results": results}
    d = ROOT / "experiments" / "registry_probe"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"cloud_availability_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")

    ok = [r["upstream_id"] for r in results if r["callable"]]
    bad = [r["upstream_id"] for r in results if not r["callable"]]
    print(f"\nACTUAL TOTAL SPEND (provider meter x frozen catalog price): ${total:.8f}")
    print(f"callable={len(ok)} not_callable={len(bad)}")
    if bad:
        print("not callable serverless:", *bad, sep="\n  - ")
    print(f"frozen: {fp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
