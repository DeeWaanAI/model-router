#!/usr/bin/env python3
"""diagnostics.py — three numbers the log already contains and the paper never reported.

Nothing here calls a model or spends money: it reads `ds:decision_log` and `ds:models` and
computes

  1. gate binding frequency — how often each gate actually excluded something, and how often it
     emptied the set. A gate that never binds is a gate whose cost is unmeasured; a gate that
     always binds is doing the routing on its own.
  2. latency percentiles per connection, from the provider-reported wall clock of real attempts.
  3. determinism — the same frozen inputs must produce the same decision (spec §2.7). This is
     checked by routing the same request twice into a throwaway copy of the store and comparing
     every semantic field of the two records.

    .venv/bin/python experiments/diagnostics.py
"""
import json, shutil, statistics, sys, tempfile
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
sys.path.insert(0, str(L5 / "router"))

from decide import Router  # noqa: E402
from store import DATASETS, Store, new_id  # noqa: E402

OUT = HERE / "results" / "derived"
DECISION_FIELDS = ("privacy_class", "task_class", "classifier_matched_rank", "gate_trace",
                   "feasible_set", "chosen", "outcome", "refusal_reason", "est_tokens_in",
                   "est_tokens_method")


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round((p / 100) * (len(xs) - 1)))))
    return round(xs[k], 3)


def gate_binding(store):
    decs = store.records("decision_log")
    per_run = defaultdict(lambda: defaultdict(Counter))
    for d in decs:
        run = d.get("run") or "(no run)"
        for g in d.get("gate_trace") or []:
            name = g["gate"]
            if g.get("excluded"):
                per_run[run][name]["excluded_something"] += 1
            if not g.get("survivors"):
                per_run[run][name]["emptied_set"] += 1
            per_run[run][name]["ran"] += 1
    agg = defaultdict(Counter)
    for run, gates in per_run.items():
        for name, c in gates.items():
            agg[name].update(c)
    rows = []
    for name in sorted(agg):
        c = agg[name]
        rows.append({"gate": name, "decisions": c["ran"],
                     "excluded_something": c["excluded_something"],
                     "emptied_set": c["emptied_set"],
                     "binding_rate": round(c["excluded_something"] / c["ran"], 4) if c["ran"] else None})
    print("gate binding frequency (all runs):")
    for r in rows:
        print(f"  {r['gate']:14} ran={r['decisions']:4} excluded>=1={r['excluded_something']:4} "
              f"emptied={r['emptied_set']:3} bind={r['binding_rate']}")
    return {"overall": rows,
            "by_run": {run: {g: dict(c) for g, c in gates.items()}
                       for run, gates in per_run.items()}}


def latency(store):
    by_model = defaultdict(list)
    names = {m["id"]: (m.get("label") or m.get("name")) for m in store.records("models")}
    for d in store.records("decision_log"):
        for a in d.get("attempts") or []:
            w = a.get("wall_clock_s")
            if w is not None:
                by_model[names.get(a["model_ref"], a["model_ref"])].append(float(w))
    rows = []
    for model, xs in sorted(by_model.items()):
        rows.append({"model": model, "n_attempts": len(xs), "p50_s": pct(xs, 50),
                     "p95_s": pct(xs, 95), "max_s": round(max(xs), 3)})
        print(f"  {model:34} n={len(xs):3} p50={rows[-1]['p50_s']}s p95={rows[-1]['p95_s']}s")
    return rows


def _fresh_copy():
    tmp = Path(tempfile.mkdtemp(prefix="diag-"))
    shutil.copytree(L5 / "datasets", tmp / "datasets")
    return Store(tmp), tmp


def determinism(store):
    """Two identical requests -> two records whose every semantic field is identical."""
    tmp_store, _ = _fresh_copy()
    text = ("Sum the amount column of expenses_small.csv and count its rows. Columns are: id, "
            "date, amount, category, vendor. This is a python data exercise on public synthetic "
            "data, no personal information. Print ONLY one JSON object: "
            '{"n_rows": <int>, "total_amount": <number>}')
    req = {"request_id": "req_determinism", "text": text}
    r = Router(store=tmp_store)
    a = r.route(req, dry_run=True, run="det")
    b = r.route(req, dry_run=True, run="det")
    ra = tmp_store.get("decision_log", a["decision_ref"])
    rb = tmp_store.get("decision_log", b["decision_ref"])
    diffs = [f for f in DECISION_FIELDS if ra.get(f) != rb.get(f)]
    print(f"\ndeterminism: {len(DECISION_FIELDS)} semantic fields compared, "
          f"{len(diffs)} differ {diffs}")
    print(f"  record ids differ (expected): {ra['id'] != rb['id']}")
    return {"fields_compared": list(DECISION_FIELDS), "fields_differing": diffs,
            "bytes_equal_except_volatile": not diffs}


def main():
    store = Store(L5)
    print("=== gate binding ===")
    g = gate_binding(store)
    print("\n=== latency (provider wall clock, real attempts only) ===")
    lat = latency(store)
    print("\n=== determinism ===")
    det = determinism(store)
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / "diagnostics.json"
    fp.write_text(json.dumps({"gates": g, "latency": lat, "determinism": det},
                             indent=1, sort_keys=True) + "\n")
    print(f"\nfrozen: {fp.relative_to(L5)}")
    ok = det["bytes_equal_except_volatile"]
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
