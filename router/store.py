#!/usr/bin/env python3
"""router.store — the only sanctioned read/write path for Layer 5 datasets.

Qafiya v1.1 §8 layout, v1.3 chained history (vendored, not reimplemented).
Schemas are read from datasets/<name>/schema.json — the frozen artifact is the
source of truth, never a copy embedded in code.
"""
import contextlib, fcntl, hashlib, json, os, threading, time
from pathlib import Path

from qafiya_core import chain

DATASETS = ["models", "capabilities", "policy", "rules", "decision_log"]
BINDING_KINDS = ("view", "claim", "edit")
_counter = [0]
_counter_lock = threading.Lock()


class StoreError(Exception):
    pass


def new_id(prefix):
    """Prefixed sortable id (OS identity rule). Never bare uuid4.

    The counter is locked: two concurrent requests must not mint the same id, or the
    append-only log would contain two records claiming one identity (RC-4.7).
    """
    with _counter_lock:
        _counter[0] += 1
        n = _counter[0]
        return f"{prefix}_{int(time.time() * 1000):013d}{n:05d}"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(obj_or_str):
    s = obj_or_str if isinstance(obj_or_str, str) else canon(obj_or_str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_FP = None


def code_fingerprint():
    """sha256 over every router/*.py, so a decision record pins the CODE that produced it
    and not only the data.

    Datasets pin policy, rules and registry versions. Without this, editing the classifier
    silently changes what a past run means while every pinned id still resolves — exactly
    what happened when keyword matching moved from substrings to word boundaries on
    2026-10-22. A mismatch between a record's fingerprint and the current code is a
    reportable fact, not an error: the record stays valid as evidence of what ran then.
    """
    global _FP
    if _FP is None:
        base = Path(__file__).resolve().parent
        h = hashlib.sha256()
        for p in sorted(base.rglob("*.py")):
            h.update(p.relative_to(base).as_posix().encode())
            h.update(p.read_bytes())
        _FP = h.hexdigest()
    return _FP


# ---------------- schema validation ----------------

def _type_ok(val, typ):
    if "|" in typ:
        opts = [t.strip() for t in typ.split("|")]
        if val is None:
            return "null" in opts
        return any(_type_ok(val, o) for o in opts if o != "null")
    if typ.startswith("enum["):
        return val in [o.strip() for o in typ[5:-1].split(",")]
    if typ.startswith("list<"):
        inner = typ[5:-1]
        return isinstance(val, list) and all(_type_ok(v, inner) for v in val)
    return {
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "id": lambda v: isinstance(v, str) and len(v) > 0,
        "timestamp": lambda v: isinstance(v, str) and len(v) > 0,
        "any": lambda v: True,
    }.get(typ, lambda v: True)(val)


def _walk(obj, parts, typ, path, errors):
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        key = head[:-2]
        if not isinstance(obj, dict) or obj.get(key) is None:
            return
        if not isinstance(obj[key], list):
            errors.append(f"{path}: expected a list")
            return
        for item in obj[key]:
            _walk(item, rest, typ, path, errors)
        return
    if not isinstance(obj, dict):
        errors.append(f"{path}: parent is not an object")
        return
    if head not in obj:
        if rest:
            # The parent is absent, so there is nothing nested to check; the parent's own
            # declaration (required, or nullable) governs whether that is an error.
            return
        # A field declared nullable MAY be absent, meaning null. Everything else is required.
        if "null" in [t.strip() for t in typ.split("|")]:
            return
        errors.append(f"missing field {path}")
        return
    val = obj[head]
    if rest:
        if val is None:
            return
        _walk(val, rest, typ, path, errors)
        return
    if not _type_ok(val, typ):
        errors.append(f"{path}: {val!r} is not {typ}")


def validate(schema, rec):
    """Check a record against its frozen schema. Raises StoreError listing every fault."""
    errors = []
    for path, typ in schema["fields"].items():
        _walk(rec, path.split("."), typ, path, errors)
    if errors:
        raise StoreError(f"{schema['dataset']}: " + "; ".join(errors))
    return True


def validate_binding(b):
    """Qafiya §7 invariants 2, 3, 6 applied at write time."""
    errors = []
    if b.get("kind") not in BINDING_KINDS:
        errors.append(f"kind must be one of {BINDING_KINDS}")
    if not b.get("sources"):
        errors.append("sources MUST be non-empty (else I1, not a pass)")
    for s in b.get("sources", []):
        if not s.get("dataset"):
            errors.append("source without dataset")
        if not s.get("record") and not s.get("filter"):
            errors.append("source without record or filter")
    for f in ("id", "target", "derivation", "statistic", "device", "authoritative_artifact"):
        if not b.get(f):
            errors.append(f"binding missing {f}")
    if "value" not in b:
        errors.append("binding missing value")
    if b.get("kind") == "claim":
        a = b.get("assertion") or {}
        for f in ("statement", "predicate", "expected"):
            if f not in a:
                errors.append(f"claim assertion missing {f}")
        if "both_sides" not in a:
            errors.append("comparisons MUST record both values")
    if b.get("kind") == "edit" and not (b.get("writeback") or {}).get("record_id"):
        errors.append("edit binding missing writeback.record_id")
    if errors:
        raise StoreError("binding: " + "; ".join(errors))
    return True


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self._schemas = {}
        for name in DATASETS:
            d = self.root / "datasets" / name
            for f in ("schema.json", "data.jsonl", "history.jsonl", "bindings.jsonl", "SUPERSEDED.json"):
                if not (d / f).exists():
                    raise StoreError(f"dataset {name} is missing {f} — run the scaffold step")
            self._schemas[name] = json.loads((d / "schema.json").read_text())

    # ---------- paths ----------
    def _dir(self, ds):
        if ds not in DATASETS:
            raise StoreError(f"unknown dataset {ds}")
        return self.root / "datasets" / ds

    @property
    def schemas(self):
        return self._schemas

    # ---------- reads ----------
    def records(self, ds, status="any"):
        out = []
        for line in (self._dir(ds) / "data.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if status != "any" and r.get("status") != status:
                continue
            out.append(r)
        return out

    def get(self, ds, rid):
        for r in self.records(ds):
            if r["id"] == rid:
                return r
        return None

    def bindings(self, ds):
        return [json.loads(l) for l in (self._dir(ds) / "bindings.jsonl").read_text().splitlines() if l.strip()]

    def superseded(self, ds):
        return json.loads((self._dir(ds) / "SUPERSEDED.json").read_text() or "[]")

    # ---------- writes ----------
    @contextlib.contextmanager
    def _locked(self, ds):
        """Serialise writers on a dataset (RC-4.7).

        A chained history has exactly one tail; two writers that read the same tail would
        both extend it and the chain would fork. flock is held for the whole
        read-tail/write-record/write-history sequence, so a second writer waits rather than
        interleaving, across threads and across processes.
        """
        with (self._dir(ds) / ".lock").open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def append(self, ds, rec):
        """Append a new record: data.jsonl + chained history entry."""
        validate(self._schemas[ds], rec)
        rid = rec["id"]
        with self._locked(ds):
            if self.get(ds, rid) is not None:
                raise StoreError(f"{ds}: id {rid} already exists (ids are immutable)")
            with (self._dir(ds) / "data.jsonl").open("a") as f:
                f.write(canon(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._hist(ds, "append", rec)
        return rec

    def set_status(self, ds, rid, status, superseded_by=None):
        """Retire/discredit a record. Atomic rewrite (Qafiya §6: temp-then-rename)."""
        with self._locked(ds):
            recs = self.records(ds)
            target = next((r for r in recs if r["id"] == rid), None)
            if target is None:
                raise StoreError(f"{ds}: no record {rid}")
            before = json.loads(json.dumps(target))
            target["status"] = status
            target["superseded_by"] = superseded_by
            p = self._dir(ds) / "data.jsonl"
            tmp = p.with_suffix(".tmp")
            tmp.write_text("".join(canon(r) + "\n" for r in recs))
            os.replace(tmp, p)
            self._hist(ds, "status", {"id": rid, "before": before, "after": target})
        return target

    def quarantine(self, ds, artifact, reason):
        """Add an artifact to SUPERSEDED.json — for WRONG artifacts only, not old ones
        (spec §9.1/§9.2). Any binding sourcing from it fails by construction."""
        with self._locked(ds):
            items = self.superseded(ds)
            entry = {"artifact": artifact, "reason": reason, "at": now()}
            items.append(entry)
            p = self._dir(ds) / "SUPERSEDED.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, indent=1) + "\n")
            os.replace(tmp, p)
            self._hist(ds, "quarantine", entry)
        return entry

    def append_binding(self, ds, binding):
        validate_binding(binding)
        with self._locked(ds):
            with (self._dir(ds) / "bindings.jsonl").open("a") as f:
                f.write(canon(binding) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._hist(ds, "binding", binding)
        return binding

    # ---------- integrity ----------
    def _hist(self, ds, op, payload):
        """Chained history is the substrate's mechanism, not this layer's (Qafiya v1.3)."""
        return chain.append(self._dir(ds) / "history.jsonl",
                            {"op": op, "record": payload, "at": now()})

    def verify_chain(self, ds):
        ok, detail = chain.verify(self._dir(ds) / "history.jsonl")
        return ok, detail

    def registry_snapshot_hash(self):
        """sha256 over the sorted active model records — pins what the router saw (spec §1.6)."""
        active = sorted((r for r in self.records("models", status="active")), key=lambda r: r["id"])
        return sha(active)


if __name__ == "__main__":
    import sys
    s = Store(Path(__file__).resolve().parent.parent)
    bad = 0
    for ds in DATASETS:
        ok, detail = s.verify_chain(ds)
        n = len(s.records(ds))
        print(f"[{'PASS' if ok else 'FAIL'}] {ds}: {detail}; {n} records")
        if not ok:
            bad += 1
    sys.exit(1 if bad else 0)
