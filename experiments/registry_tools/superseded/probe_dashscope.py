#!/usr/bin/env python3
"""Probe Alibaba DashScope for a callable cheap cloud arm.

Tries the OpenAI-compatible endpoint on both regions (international/Singapore and
mainland/Beijing) against the candidate model names, one minimal call each. Reports
which combinations work, with the provider's own metered token counts.

The key is loaded by explicit path and NEVER printed — name and length only.
DashScope prices are not yet in the frozen registry, so this script reports tokens and
NOT dollars; a price with provenance must be recorded before this arm can be registered.
"""
import json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.http import post_json  # noqa: E402

PROMPT = "Reply with one word: ready"
MAX_TOKENS = 8

HOSTS = {
    # Measured 2026-10-22: this key is for the INTERNATIONAL (Singapore) region. The
    # mainland endpoint rejects it with "Incorrect API key provided" for every model, so
    # it is not probed again — that would just waste calls.
    "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}
# `qwen2.5-7b-instruct` was the owner's first choice but returned "Access denied" on this
# account; `qwen-turbo` returned a request-limit error, which may be transient, so both
# are retried alongside other cheap current aliases.
MODELS = ["qwen-turbo", "qwen-flash", "qwen-plus", "qwen2.5-7b-instruct",
          "qwen-turbo-latest", "qwen3-8b"]


def load_key():
    env = os.environ.get("DASHSCOPE_API_KEY")
    if env:
        return env.strip(), "environment"
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("DASHSCOPE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v, str(p)
    raise SystemExit("DASHSCOPE_API_KEY not found")


def main():
    key, src = load_key()
    print(f"key: name=DASHSCOPE_API_KEY length={len(key)} source={Path(src).name} (value not printed)")
    if len(key) > 80:
        print("  note: unusually long for an API key — checking for wrapping/whitespace artefacts")

    results = []
    for region, base in HOSTS.items():
        for model in MODELS:
            body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
                    "max_tokens": MAX_TOKENS, "temperature": 0, "stream": False}
            t0 = time.time()
            try:
                status, res = post_json(base + "/chat/completions", body,
                                        headers={"Authorization": f"Bearer {key}"}, timeout=90)
                wall = round(time.time() - t0, 3)
                ch = (res.get("choices") or [{}])[0]
                u = res.get("usage") or {}
                r = {"region": region, "model": model, "callable": True, "http_status": status,
                     "finish_reason": ch.get("finish_reason"), "wall_clock_s": wall,
                     "tokens_in": u.get("prompt_tokens"), "tokens_out": u.get("completion_tokens"),
                     "response_text": (ch.get("message") or {}).get("content"),
                     "served_model": res.get("model"), "usage_raw": u}
                print(f"  OK   {region:5} {model:22} {wall:>5}s in={u.get('prompt_tokens')} "
                      f"out={u.get('completion_tokens')} finish={ch.get('finish_reason')} "
                      f"{(ch.get('message') or {}).get('content')!r}")
            except Exception as e:
                msg = str(e)
                r = {"region": region, "model": model, "callable": False, "error": msg[:400]}
                short = msg.split(":", 1)[-1].strip().replace("\n", " ")[:150]
                print(f"  FAIL {region:5} {model:22} {short}")
            results.append(r)

    ok = [r for r in results if r["callable"]]
    out = {"purpose": "DashScope availability probe (one minimal call per host x model)",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "prompt": PROMPT, "max_tokens": MAX_TOKENS,
           "key_name": "DASHSCOPE_API_KEY", "key_length": len(key),
           "note": "no dollar cost computed: DashScope prices are not yet frozen in ds:models",
           "results": results}
    d = ROOT / "experiments" / "registry_probe"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"dashscope_availability_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")
    print(f"\ncallable: {len(ok)}/{len(results)}")
    for r in ok:
        print(f"  -> {r['region']} {r['model']} served_as={r.get('served_model')}")
    print(f"frozen: {fp.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
