#!/usr/bin/env python3
"""Build and freeze the experiment-1/3 battery: tasks whose ground truth is COMPUTED, not
hand-written and not model-judged.

Every expected answer is derived from the corpus by this script, so a grader can score an
answer without an LLM judge and without a human. The corpus is copied into the battery
directory and checksummed, so the experiment does not depend on another layer's folder
staying put — and the Qafiya checksums catch any drift.

Six items, all of one shape: "return ONLY valid JSON with these fields". That makes the
three measured quantities for `ds:capabilities` fall out of the same run:
  accuracy          fraction of expected fields matched exactly
  schema_compliance fraction of replies that are one valid JSON object
  termination_rate  fraction that finish before the token budget
Item 4 (800-row total) is deliberately the case the upstream live runs measured at 0/12, so
the hazard is reproduced rather than avoided; item 5 is the multi-document selective case.
"""
import csv, hashlib, json, shutil, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
CORPUS = L5.parent / "02-view-renderer/qafiya-ref/experiments/efficiency-experiment/corpus/tabular"
BCORPUS = HERE / "battery" / "corpus"

SOURCES = ["expenses_small.csv", "expenses_medium.csv", "expenses_large.csv",
           "invoices_small.csv", "invoices_medium.csv"]
ASK = ("Reply with ONLY valid JSON, no prose, no code fences, exactly these fields: ")


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def money(x):
    return round(float(x), 2)


def gt_totals(path):
    rs = rows(path)
    return {"n_rows": len(rs), "total": money(sum(float(r["amount"]) for r in rs))}


def gt_max(path):
    rs = rows(path)
    top = max(rs, key=lambda r: float(r["amount"]))
    # the corpus columns are id,date,amount,category,vendor — "source" is the row id
    return {"max_amount": money(top["amount"]), "max_source": top["id"]}


def gt_groupby(path):
    rs = rows(path)
    agg = defaultdict(float)
    for r in rs:
        agg[r["category"]] += float(r["amount"])
    return {"by_category": {k: money(v) for k, v in sorted(agg.items())}}


def gt_top_vendor(path):
    rs = rows(path)
    agg = defaultdict(float)
    for r in rs:
        agg[r["vendor"]] += float(r["amount"])
    v, amt = max(agg.items(), key=lambda kv: kv[1])
    return {"top_vendor": v, "top_vendor_total": money(amt)}


def gt_selective(files, category):
    out = {}
    for fn in files:
        rs = [r for r in rows(BCORPUS / fn) if r["category"] == category]
        out[fn.replace(".csv", "")] = money(sum(float(r["amount"]) for r in rs))
    return {"travel_by_file": out, "travel_total": money(sum(out.values()))}


def table_text(files):
    return "\n\n".join(
        f"=== {fn} ===\n" + (BCORPUS / fn).read_text().strip() for fn in files)


def main():
    if BCORPUS.exists():
        shutil.rmtree(BCORPUS)
    BCORPUS.mkdir(parents=True)
    for fn in SOURCES:
        src = CORPUS / fn
        if not src.exists():
            raise SystemExit(f"missing upstream corpus file {src}")
        shutil.copy(src, BCORPUS / fn)

    smed, slar = ["expenses_medium.csv"], ["expenses_large.csv"]
    items = [
        {"id": "b_total_small", "prompt_class": "tabular_summary",
         "files": ["expenses_small.csv"],
         "prompt": "Sum the amount column of this CSV.\n" + ASK + '{"n_rows": int, "total": number}\n\n'
                   + table_text(["expenses_small.csv"]),
         "expected": gt_totals(BCORPUS / "expenses_small.csv")},
        {"id": "b_max_medium", "prompt_class": "tabular_summary",
         "files": smed,
         "prompt": "Find the largest amount and its source row.\n" + ASK
                   + '{"max_amount": number, "max_source": string}\n\n' + table_text(smed),
         "expected": gt_max(BCORPUS / "expenses_medium.csv")},
        {"id": "b_groupby_medium", "prompt_class": "tabular_summary",
         "files": smed,
         "prompt": "Sum the amount column grouped by category.\n" + ASK
                   + '{"by_category": {"<category>": number}}\n\n' + table_text(smed),
         "expected": gt_groupby(BCORPUS / "expenses_medium.csv")},
        {"id": "b_total_large", "prompt_class": "tabular_summary",
         "files": slar,
         "prompt": "Sum the amount column of this 800-row CSV.\n" + ASK
                   + '{"n_rows": int, "total": number}\n\n' + table_text(slar),
         "expected": gt_totals(BCORPUS / "expenses_large.csv")},
        {"id": "b_top_vendor_large", "prompt_class": "tabular_summary",
         "files": slar,
         "prompt": "Find the vendor with the largest total.\n" + ASK
                   + '{"top_vendor": string, "top_vendor_total": number}\n\n' + table_text(slar),
         "expected": gt_top_vendor(BCORPUS / "expenses_large.csv")},
        {"id": "b_selective_travel", "prompt_class": "multi_doc_selective",
         "files": ["expenses_small.csv", "expenses_medium.csv", "invoices_small.csv",
                   "invoices_medium.csv"],
         "prompt": "Across ALL files below, total the travel category per file and overall.\n" + ASK
                   + '{"travel_by_file": {"<file_stem>": number}, "travel_total": number}\n\n'
                   + table_text(["expenses_small.csv", "expenses_medium.csv",
                                 "invoices_small.csv", "invoices_medium.csv"]),
         "expected": gt_selective(["expenses_small.csv", "expenses_medium.csv",
                                   "invoices_small.csv", "invoices_medium.csv"], "travel")},
    ]

    out = HERE / "battery" / "items.jsonl"
    out.write_text("".join(json.dumps(it, sort_keys=True) + "\n" for it in items))
    man = {"items": out.name, "n_items": len(items),
           "items_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
           "corpus": {fn: {"sha256": hashlib.sha256((BCORPUS / fn).read_bytes()).hexdigest(),
                           "bytes": (BCORPUS / fn).stat().st_size,
                           "rows": len(rows(BCORPUS / fn))} for fn in SOURCES},
           "corpus_source": str(CORPUS.relative_to(L5.parent)),
           "ground_truth": "computed by make_battery.py from the copied corpus; no LLM judge, "
                           "no human annotation",
           "by_class": {},
           "prompt_chars": {it["id"]: len(it["prompt"]) for it in items}}
    for it in items:
        man["by_class"][it["prompt_class"]] = man["by_class"].get(it["prompt_class"], 0) + 1
    (HERE / "battery" / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")

    print(f"frozen {len(items)} items -> {out.relative_to(L5)}")
    for fn, v in man["corpus"].items():
        print(f"  {fn:22} rows={v['rows']:<5} sha256={v['sha256'][:12]}…")
    for it in items:
        print(f"  {it['id']:20} {it['prompt_class']:20} chars={len(it['prompt']):>6} "
              f"expected={json.dumps(it['expected'])[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
