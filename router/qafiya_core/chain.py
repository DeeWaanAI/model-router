#!/usr/bin/env python3
"""qafiya_core.chain — hash-chained history (spec v1.3 reference implementation).

Drop-in for qafiya-core: append entries with chaining, verify any dataset's
history.jsonl. Zero dependencies; canonical JSON per the spec delta.
"""
import hashlib, json
from pathlib import Path

GENESIS = "0" * 64


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev_hash: str, entry: dict) -> str:
    return hashlib.sha256((prev_hash + canon(entry)).encode()).hexdigest()


def append(history_path: Path, entry: dict) -> dict:
    """Append an entry with prev_hash/entry_hash. Returns the stored record."""
    entries = _read(history_path)
    prev = entries[-1]["entry_hash"] if entries else GENESIS
    stored = dict(entry)
    stored["prev_hash"] = prev
    stored["entry_hash"] = entry_hash(prev, entry)
    with open(history_path, "a") as f:
        f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def verify(history_path: Path):
    """Recompute the chain. Returns (ok, detail). Never repairs."""
    entries = _read(history_path)
    prev = GENESIS
    for i, e in enumerate(entries):
        body = {k: v for k, v in e.items() if k not in ("prev_hash", "entry_hash")}
        if e.get("prev_hash") != prev:
            return False, f"entry {i}: prev_hash mismatch (chain broken)"
        if e.get("entry_hash") != entry_hash(prev, body):
            return False, f"entry {i}: entry_hash mismatch (content tampered)"
        prev = e["entry_hash"]
    return True, f"{len(entries)} entries verified"


def migrate(history_path: Path):
    """Add chain fields to a pre-v1.3 history file, in place (content untouched)."""
    entries = _read(history_path)
    if not entries or "entry_hash" in entries[0]:
        return 0
    prev, out = GENESIS, []
    for e in entries:
        stored = dict(e); stored["prev_hash"] = prev
        stored["entry_hash"] = entry_hash(prev, e)
        out.append(stored); prev = stored["entry_hash"]
    tmp = history_path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in out))
    tmp.replace(history_path)
    return len(out)


def _read(p: Path):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
