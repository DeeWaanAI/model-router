#!/usr/bin/env python3
"""verify_two_way.py — two-way claim-map verifier for the Layer 5 paper.

Direction A (data): for every entry in datapoints.json, re-read the frozen raw
artifact and check the value at the key path equals `expect` (within `tol`).
Direction B (paper): the entry's `marker` text must appear verbatim in the
current draft (paper/draft.tex).

Entry kinds:
  json        {source, path, expect, tol?}          dotted path into a JSON file
  jsonl-find  {source, find:{k:v,...}, path, expect, tol?}
  jsonl-count {source, where:{k:v,...}, expect}     count matching lines
  jsonl-contains-count {source, where, field, contains, expect}
  jsonl-range {source, field, where, expect:[lo,hi]}
  jsonl-len   {source, expect}
  ratio       {num:{source,path}, den:{source,path}, expect, tol}
  csv-sum     {source, column, expect_rows, expect_sum, tol}
  text-in-file {source, text}                        frozen stdout/log artifact
  glob-json   like json, but source may contain '*' (latest match wins)

Sources are repo-relative paths to frozen artifacts (raw run outputs, frozen
analysis results, frozen corpora). Audit reports are used only for claims
ABOUT the audit. Convention note: experiments/*/results/derived/*.json are
frozen outputs of committed scripts (spine.py, baselines.py, diagnostics.py,
fault_injection.py, rules_audit.py, annotate.py runs); the spine recomputes
them from raw, which is the deeper check.

Exit 0 only when every entry passes both directions.
"""
import csv, glob, json, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # 05-model-router/
MAP = Path(__file__).resolve().parent
DRAFT = ROOT / "paper" / "draft.tex"

entries = json.loads((MAP / "datapoints.json").read_text())["datapoints"]
draft = re.sub(r"\s+", " ", DRAFT.read_text())      # LaTeX line wrapping is not content
fails = []


def load(source):
    if "*" in source:
        matches = sorted(glob.glob(str(ROOT / source)))
        if not matches:
            raise FileNotFoundError(source)
        return Path(matches[-1])
    return ROOT / source


def dig(obj, path):
    segs = path if isinstance(path, list) else str(path).split(".")
    for seg in segs:
        if seg == "@len":
            obj = len(obj)
        elif isinstance(obj, list):
            obj = obj[int(seg)]
        else:
            obj = obj[seg]
    return obj


def resolve(spec):
    """A value spec: const, or {source, path} for JSON, or {source, find, path} for JSONL."""
    if "const" in spec:
        return spec["const"]
    src = load(spec["source"])
    if "find" in spec:
        for line in src.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rec = r.get("record", r)
            if all(rec.get(fk) == fv or dig(rec, fk) == fv for fk, fv in spec["find"].items()):
                return dig(rec, spec["path"])
        raise KeyError(f"no matching line in {spec['source']}")
    return dig(json.loads(src.read_text()), spec["path"])


def close(a, b, tol):
    if isinstance(a, str) or isinstance(b, str) or isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if a is None or b is None:
        return a is b
    fa, fb = float(a), float(b)
    if math.isnan(fa) and math.isnan(fb):
        return True
    return abs(fa - fb) <= (tol if tol is not None else 1e-9)


def check_data(e):
    k = e["kind"]
    if k == "ratio":
        a = float(resolve(e["num"]))
        b = float(resolve(e["den"]))
        v = a / b
        return None if close(v, e["expect"], e.get("tol", 1e-9)) else f"ratio {v} != {e['expect']}"
    src = load(e["source"])
    if not src.exists():
        return f"missing source {e['source']}"
    if k == "json" or k == "glob-json":
        v = dig(json.loads(src.read_text()), e["path"])
        return None if close(v, e["expect"], e.get("tol")) else f"value {v} != expect {e['expect']}"
    if k == "json-find":
        lst = dig(json.loads(src.read_text()), e["list_path"])
        for r in lst:
            if all(r.get(fk) == fv for fk, fv in e["where"].items()):
                v = dig(r, e["path"])
                return None if close(v, e["expect"], e.get("tol")) else f"value {v} != {e['expect']}"
        return "no matching element"
    if k == "json-list-count":
        lst = dig(json.loads(src.read_text()), e["list_path"])
        n = sum(1 for r in lst if all(r.get(fk) == fv for fk, fv in e["where"].items()))
        return None if n == e["expect"] else f"count {n} != {e['expect']}"
    if k == "json-distinct":
        lst = dig(json.loads(src.read_text()), e["list_path"])
        vals = {json.dumps(dig(r, e["path"]), sort_keys=True) for r in lst
                if all(r.get(fk) == fv for fk, fv in e["where"].items())}
        return None if len(vals) == e["expect"] else f"distinct {len(vals)} != {e['expect']}"
    if k == "jsonl-find":
        for line in src.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rec = r.get("record", r)
            if all(rec.get(fk) == fv for fk, fv in e["find"].items()):
                v = dig(rec, e["path"])
                return None if close(v, e["expect"], e.get("tol")) else f"value {v} != {e['expect']}"
        return "no matching line"
    if k == "jsonl-count":
        n = sum(1 for line in src.read_text().splitlines() if line.strip()
                and all(json.loads(line).get(fk) == fv for fk, fv in e["where"].items()))
        return None if n == e["expect"] else f"count {n} != {e['expect']}"
    if k == "jsonl-contains-count":
        n = 0
        for line in src.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if all(r.get(fk) == fv for fk, fv in e["where"].items()) and \
               e["contains"] in str(r.get(e["field"])):
                n += 1
        return None if n == e["expect"] else f"count {n} != {e['expect']}"
    if k == "jsonl-range":
        vals = [json.loads(l)[e["field"]] for l in src.read_text().splitlines() if l.strip()
                and all(json.loads(l).get(fk) == fv for fk, fv in e["where"].items())
                and json.loads(l).get(e["field"]) is not None]
        if not vals:
            return "no values"
        return None if [min(vals), max(vals)] == e["expect"] else \
            f"range [{min(vals)},{max(vals)}] != {e['expect']}"
    if k == "jsonl-len":
        n = sum(1 for l in src.read_text().splitlines() if l.strip())
        return None if n == e["expect"] else f"len {n} != {e['expect']}"
    if k == "csv-sum":
        with open(src) as f:
            rows = list(csv.DictReader(f))
        s = round(sum(float(r[e["column"]]) for r in rows), 2)
        if len(rows) != e["expect_rows"]:
            return f"rows {len(rows)} != {e['expect_rows']}"
        return None if close(s, e["expect_sum"], e.get("tol", 0.01)) else f"sum {s} != {e['expect_sum']}"
    if k == "text-in-file":
        return None if e["text"] in src.read_text() else "text not found in artifact"
    return f"unknown kind {k}"


for e in entries:
    err = check_data(e)
    if err:
        fails.append((e["id"], "DATA: " + err))
    if e["marker"] not in draft:
        fails.append((e["id"], "PAPER: marker not in draft: " + e["marker"][:70]))

if fails:
    for fid, msg in fails:
        print("FAIL", fid, msg)
    print(f"\n{len(fails)} failures of {len(entries)} entries")
    sys.exit(1)

# Direction B for the semantic map: every full_quote (and caption quote) must anchor in the
# current draft. Quotes are whitespace-normalized and LaTeX escape chars stripped on both sides.
sem = json.loads((MAP / "semantic-claims.json").read_text())
def norm(t):
    t = t.replace("{,}", ",")               # before textbf unwrap (nested braces)
    t = re.sub(r"\\(textbf|emph|code)\{([^}]*)\}", r"\2", t)
    t = t.replace("\\$", "\x00")            # escaped currency dollar is not math mode
    t = t.replace("\\,", "").replace("$\\times$", "x").replace("$\\rightarrow$", "->")
    t = t.replace("$\\ge$", ">=").replace("``", '"').replace("''", '"')
    t = re.sub(r"\$([^$]*)\$", r"\1", t)    # unwrap inline math
    return re.sub(r"\s+", " ", t).replace("\\", "").replace("\x00", "$")
draft_n = norm(DRAFT.read_text())
sfails = []
for c in sem["claims"] + sem["captions"]:
    q = norm(c["full_quote"])
    if q not in draft_n:
        sfails.append((c["id"], q[:80]))
if sfails:
    for cid, q in sfails:
        print("FAIL", cid, "quote not in draft:", q)
    print(f"\n{len(sfails)} semantic-quote failures")
    sys.exit(1)
print(f"OK: {len(entries)} datapoints verified against frozen artifacts and the current draft")
print(f"OK: {len(sem['claims'])} semantic claims + {len(sem['captions'])} captions anchored in the draft")
