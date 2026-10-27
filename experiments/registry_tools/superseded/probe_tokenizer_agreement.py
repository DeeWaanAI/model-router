#!/usr/bin/env python3
"""Does the frozen tokenizer actually match the provider's meter?

spec §3.3 requires per-variant token estimates and §10.4 requires the estimate-vs-actual
error to be reported. This measures it, using a DIFFERENCE so the chat template's fixed
overhead cancels out: send prompt A, then A+DELTA, and compare the provider's metered
token increase against the frozen tokenizer's count of DELTA alone.

Cost: two minimal calls per model. Uses the real battery corpus as DELTA, so the result
applies to the text the experiments actually send.
"""
import json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.http import post_json  # noqa: E402
import tokens as T  # noqa: E402

CORPUS = ROOT.parent / ("02-view-renderer/qafiya-ref/experiments/efficiency-experiment/"
                        "corpus/tabular/expenses_medium.csv")
BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
CANDIDATES = [("qwen-flash", "tokenizer/qwen25_tokenizer.json"),
              ("qwen-flash", "tokenizer/llama3_tokenizer.json")]


def key():
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip().startswith("DASHSCOPE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DASHSCOPE_API_KEY not found")


def metered(prompt, model, k):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1, "temperature": 0, "stream": False}
    _, res = post_json(BASE + "/chat/completions", body,
                       headers={"Authorization": f"Bearer {k}"}, timeout=120)
    return (res.get("usage") or {}).get("prompt_tokens")


def main():
    k = key()
    print(f"key: name=DASHSCOPE_API_KEY length={len(k)} (value not printed)")
    if not CORPUS.exists():
        raise SystemExit(f"corpus missing: {CORPUS}")
    delta = CORPUS.read_text()
    a = "Summarise the totals in this table."
    b = a + "\n" + delta

    ma, mb = metered(a, "qwen-flash", k), metered(b, "qwen-flash", k)
    provider_delta = mb - ma
    print(f"provider meter: prompt A={ma} tokens, A+corpus={mb} tokens -> delta={provider_delta}")
    print(f"corpus: {CORPUS.name} ({len(delta)} chars)")

    out = {"purpose": "frozen-tokenizer vs provider-meter agreement (template overhead cancelled)",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "provider": "dashscope-intl", "model": "qwen-flash",
           "corpus": str(CORPUS), "corpus_chars": len(delta),
           "metered_prompt_a": ma, "metered_prompt_a_plus_corpus": mb,
           "provider_delta_tokens": provider_delta, "candidates": []}
    for model, ref in CANDIDATES:
        mine = T.count(delta, ref)
        err = (mine - provider_delta) / provider_delta if provider_delta else None
        out["candidates"].append({"tokenizer_ref": ref, "my_tokens": mine,
                                  "abs_error_tokens": mine - provider_delta,
                                  "relative_error": round(err, 5) if err is not None else None})
        print(f"  {ref:38} mine={mine:6}  error={mine - provider_delta:+6} "
              f"({100*err:+.2f}%)")

    best = min(out["candidates"], key=lambda c: abs(c["abs_error_tokens"]))
    out["best_match"] = best["tokenizer_ref"]
    print(f"\nbest match for qwen-flash: {best['tokenizer_ref']} "
          f"({100*best['relative_error']:+.2f}%)")
    d = ROOT / "experiments" / "registry_probe"
    fp = d / f"tokenizer_agreement_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")
    print(f"frozen: {fp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
