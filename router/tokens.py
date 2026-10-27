#!/usr/bin/env python3
"""Per-variant token counting (spec §3.3).

est_tokens MUST come from the variant's own tokenizer, never a shared one and never
character or word counts: the same text measured 29.4% cheaper under the Llama tokenizer
than the Qwen one (tokenizer/manifest.json cross_check, reproducing the upstream
efficiency-experiment ratio of 29.0%). A shared estimate would mis-rank cloud models.

This module FAILS CLOSED. A missing tokenizer, a checksum mismatch against
tokenizer/manifest.json, or an unavailable `tokenizers` package raises — it never
substitutes an approximation, because an approximation would silently corrupt every
cost estimate and every gate-3 decision downstream.

Requires the layer venv:  .venv/bin/python
"""
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tokenizer" / "manifest.json"

_CACHE = {}
_MANIFEST = None


class TokenizerError(Exception):
    pass


def _manifest():
    global _MANIFEST
    if _MANIFEST is None:
        if not MANIFEST.exists():
            raise TokenizerError(f"no frozen tokenizer manifest at {MANIFEST}")
        _MANIFEST = {t["file"]: t for t in json.loads(MANIFEST.read_text())["tokenizers"]}
    return _MANIFEST


def _load(ref):
    """ref is a tokenizer_ref from ds:models, e.g. 'tokenizer/llama3_tokenizer.json'."""
    if ref in _CACHE:
        return _CACHE[ref]
    try:
        from tokenizers import Tokenizer
    except ImportError as e:
        raise TokenizerError(
            "the `tokenizers` package is required and missing; run .venv/bin/python "
            "(see requirements.txt). Refusing to fall back to a character count.") from e
    man = _manifest().get(ref)
    if man is None:
        raise TokenizerError(f"{ref} is not in the frozen tokenizer manifest")
    path = ROOT / ref
    if not path.exists():
        raise TokenizerError(f"tokenizer file missing: {path}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["sha256"]:
        raise TokenizerError(f"tokenizer {ref} checksum mismatch: manifest {man['sha256'][:16]}… "
                             f"got {got[:16]}… — the frozen artifact changed")
    _CACHE[ref] = Tokenizer.from_file(str(path))
    return _CACHE[ref]


def count(text, tokenizer_ref):
    return len(_load(tokenizer_ref).encode(text).ids)


def count_or_bound(text, tokenizer_ref):
    """(tokens, method). A user connection need not name a tokenizer — asking someone to
    pick a BPE file is not a reasonable setup step. When none is declared we use a
    deliberately conservative upper bound (3 characters per token, where English is ~4) and
    say so in the decision record. When one IS declared, a missing or altered file still
    fails closed: the estimate is then a claim about a specific frozen artifact."""
    if not tokenizer_ref:
        n = max(1, (len(text or "") + 2) // 3)
        return n, "char_div_3_upper_bound"
    return count(text, tokenizer_ref), f"tokenizer:{tokenizer_ref}"


def count_for(text, model):
    return count(text, model["tokenizer_ref"])


def checksum_report():
    """Every tokenizer referenced by the manifest, verified. Used by the audit."""
    out = []
    for ref, man in _manifest().items():
        try:
            _load(ref)
            out.append({"ref": ref, "sha256_ok": True, "vocab_size": man["vocab_size"]})
        except TokenizerError as e:
            out.append({"ref": ref, "sha256_ok": False, "error": str(e)})
    return out


if __name__ == "__main__":
    import sys
    sample = "source,amount\ntravel,120.50\nfood,80.00\nReply with ONLY valid JSON."
    bad = 0
    for r in checksum_report():
        print(f"[{'PASS' if r['sha256_ok'] else 'FAIL'}] {r['ref']}: {r}")
        if not r["sha256_ok"]:
            bad += 1
    for r in _manifest():
        print(f"{r}: {count(sample, r)} tokens for the {len(sample)}-char sample")
    sys.exit(1 if bad else 0)
