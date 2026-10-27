#!/usr/bin/env python3
"""Fetch the Together model catalog once and freeze it as the registry's provenance
artifact (spec §1.2: prices are records with provenance).

The API key is loaded from .env by explicit path and is NEVER printed — this script
reports the variable name and its length only (HANDOFF §7).
"""
import json, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_key():
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("TOGETHER_AI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v, p
    raise SystemExit("TOGETHER_AI_API_KEY not found in .env")


def price_of(m):
    """Together's pricing.input/output are ALREADY USD per million tokens (spec §3.1).
    Do not multiply by 1e6. Some entries use prompt/completion instead."""
    p = m.get("pricing") or {}
    def num(*keys):
        for k in keys:
            v = p.get(k)
            if v not in (None, "", "0"):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return 0.0 if any(k in p for k in keys) else None
    return num("input", "prompt"), num("output", "completion")


def main():
    key, src = load_key()
    print(f"key: name=TOGETHER_AI_API_KEY length={len(key)} source={src.name} (value not printed)")

    req = urllib.request.Request(
        "https://api.together.xyz/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 "User-Agent": "deewaanai-l5-router/1.0 (spec.md §1.2 registry provenance)"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            status = r.status
            models = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        print(f"GET /v1/models -> HTTP {e.code} {e.reason}")
        print(f"response body: {body}")
        print(f"key shape: length={len(key)} prefix={key[:3]!r} has_whitespace={key != key.strip()}")
        return 2
    print(f"GET /v1/models -> HTTP {status}; {len(models)} models")

    out = ROOT / "experiments" / "catalog"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    fp = out / f"together_models_{stamp}.json"
    fp.write_text(json.dumps(models, indent=1, sort_keys=True) + "\n")
    print(f"frozen raw artifact: {fp.relative_to(ROOT)}")

    by_type = {}
    for m in models:
        by_type[m.get("type", "?")] = by_type.get(m.get("type", "?"), 0) + 1
    print("by type:", json.dumps(by_type, sort_keys=True))

    chat = [m for m in models if m.get("type") == "chat"]
    priced = []
    for m in chat:
        i, o = price_of(m)
        if i is None:
            continue
        priced.append((i, o, m.get("id"), m.get("display_name"),
                       m.get("context_length"), sorted(m.get("supported_parameters") or [])))
    paid = [p for p in priced if p[0] > 0]
    free = [p for p in priced if p[0] == 0]
    print(f"chat={len(chat)} priced={len(priced)} paid={len(paid)} zero_priced={len(free)}")
    if paid:
        lo, hi = min(paid)[0], max(paid)[0]
        print(f"paid input price spread: ${lo}/Mtok -> ${hi}/Mtok ({hi/lo:.0f}x)" if lo else
              f"paid input max ${hi}/Mtok")

    paid_sorted = sorted(paid)
    print("\n-- 6 cheapest paid --")
    for i, o, mid, dn, ctx, _ in paid_sorted[:6]:
        print(f"  in=${i}/Mtok out=${o}/Mtok ctx={ctx} {mid}")
    print("-- 6 most expensive paid --")
    for i, o, mid, dn, ctx, _ in paid_sorted[-6:]:
        print(f"  in=${i}/Mtok out=${o}/Mtok ctx={ctx} {mid}")

    print("\n-- candidates matching the arms we need --")
    for pat in ("llama-3.3-70b", "llama-3.1-8b", "llama-3.2", "qwen3", "deepseek-r1", "gpt-oss"):
        hits = [p for p in priced if pat in (p[2] or "").lower()]
        for i, o, mid, dn, ctx, sp in sorted(hits)[:4]:
            reasoning = "reasoning" in sp or "reasoning" in mid.lower()
            print(f"  [{pat}] in=${i} out=${o} ctx={ctx} reasoning={reasoning} {mid}")

    print("\nfrozen artifact written; no key value printed anywhere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
