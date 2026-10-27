#!/usr/bin/env python3
"""One real cloud call through the Together backend, to prove the paid path works before
any experiment spends money. Costs a small fraction of one cent and reports the actual
figure computed from the provider's own meter.

Why this exists: the cloud path had only ever been exercised against a stub, and this
layer has already been bitten once by an untested transport assumption — Together returns
HTTP 403 for the default Python User-Agent (spec §13.2). An unexercised backend is a
declaration, not a working path.

Freezes the raw response as provenance. The API key is never printed.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.together import TogetherBackend, load_key  # noqa: E402
import cost as K  # noqa: E402
from store import Store  # noqa: E402

PROMPT = "Reply with exactly one word: ready"


def main():
    store = Store(ROOT)
    key = load_key()
    print(f"key: name=TOGETHER_AI_API_KEY length={len(key)} (value not printed)")

    variants = [m for m in store.records("models", status="active") if m["locus"] == "cloud"]
    if not variants:
        print("no cloud variants registered")
        return 2
    # cheapest cloud variant = smallest possible spend for this check
    variants.sort(key=lambda m: (m["price"]["input_per_mtok"] + m["price"]["output_per_mtok"], m["id"]))
    model = variants[0]
    print(f"variant: {model['upstream_id']} in=${model['price']['input_per_mtok']}/Mtok "
          f"out=${model['price']['output_per_mtok']}/Mtok budget={model['output_budget_tokens']}")

    be = TogetherBackend()
    t0 = time.time()
    try:
        res = be.complete(model, PROMPT)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    wall = time.time() - t0

    actual = K.actual_cost_usd(model, res["tokens_in"], res["tokens_out"])
    out = {
        "purpose": "cloud backend smoke test (one call)",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_ref": model["id"], "upstream_id": model["upstream_id"],
        "prompt": PROMPT, "prompt_tokens_estimated_note": "not used; metered values below",
        "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
        "finish_reason": res["finish_reason"], "http_status": res["http_status"],
        "wall_clock_s": round(wall, 3), "actual_cost_usd": actual,
        "response_text": res["text"],
        "raw_response": res["raw"],
    }
    d = ROOT / "experiments" / "registry_probe"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"cloud_smoke_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")

    print(f"HTTP {res['http_status']} finish={res['finish_reason']} "
          f"tokens_in={res['tokens_in']} tokens_out={res['tokens_out']} wall={round(wall,3)}s")
    print(f"response: {res['text']!r}")
    print(f"ACTUAL COST (provider meter x frozen price): ${actual:.8f}")
    print(f"frozen: {fp.relative_to(ROOT)}")
    ok = res["http_status"] == 200 and res["tokens_in"] > 0 and res["finish_reason"] == "stop"
    print(f"[{'PASS' if ok else 'FAIL'}] cloud backend returned a metered completion")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
