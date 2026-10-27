#!/usr/bin/env python3
"""Battery v2: every computational task exists TWICE, as an paired pair.

  *_inhead  the model is asked for the number directly. This is the hazard the upstream
            live runs measured at 0/12 and this layer re-measured as every-arm-wrong.
  *_code    the model is asked for a Python program; the harness RUNS it and the program's
            printed output IS the answer. This is the production architecture the owner
            specified on 2026-10-23: whenever code can do the job, code does the job.

Same corpus, same ground truth, same expected JSON — so the only difference is the
architecture, and the comparison is a measurement rather than an argument.

Three fixes over battery v1, all of them mine:
  * column names are stated exactly (`amount`, `id`, `category`, `vendor`); v1 asked for
    "max_source" when the column is `id`, and reasonable models answered the vendor name;
  * tie-breaks and rounding are specified, so grading is exact and not a judgement call;
  * prompts carry a public marker, because v1's unmarked benchmark prompts failed CLOSED to
    private, so the router refused them and the frontier could not be measured at all —
    an artifact of my battery, not a property of the system.

Ground truth is COMPUTED here from the copied corpus. No LLM judge, no human annotation.
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(HERE))

from make_battery import (BCORPUS, SOURCES, gt_groupby, gt_max, gt_selective, gt_top_vendor,
                          gt_totals)

SELECTIVE = ["expenses_small.csv", "expenses_medium.csv", "invoices_small.csv",
             "invoices_medium.csv"]

INHEAD_ASK = ("This is a python data exercise on public synthetic data, no personal information. "
              "Reply with ONLY valid JSON, no prose, no code fences, exactly this shape: ")
CODE_ASK = ("This is a python data exercise on public synthetic data, no personal information. "
            "Write a Python 3 program using the standard library only (csv, json, statistics; "
            "no third-party packages, no network). The named CSV files are already in the "
            "working directory. The program must print ONLY one JSON object, no other text, "
            "exactly this shape: ")
CONVENTIONS = ("Rounding: round every monetary value to 2 decimals. Ties: if several rows share "
               "the extreme value, choose the one whose `id` is smallest lexicographically; if "
               "several vendors tie, choose the alphabetically first `vendor`. "
               "Columns are: id, date, amount, category, vendor. ")


def build():
    B = BCORPUS
    specs = [
        ("total_small", "expenses_small.csv", gt_totals(B / "expenses_small.csv"),
         "Sum the `amount` column of expenses_small.csv and count its data rows.",
         '{"n_rows": <int>, "total_amount": <number>}', ["expenses_small.csv"]),
        ("max_medium", "expenses_medium.csv", gt_max(B / "expenses_medium.csv"),
         "Find the largest `amount` in expenses_medium.csv and report the `id` of that row.",
         '{"max_amount": <number>, "row_id_of_max": <string>}', ["expenses_medium.csv"],
         lambda d: {"max_amount": d["max_amount"], "row_id_of_max": d["max_source"]}),
        ("groupby_medium", "expenses_medium.csv",
         {"sum_by_category": gt_groupby(B / "expenses_medium.csv")["by_category"]},
         "Sum the `amount` column of expenses_medium.csv grouped by `category`.",
         '{"sum_by_category": {"<category>": <number>}}', ["expenses_medium.csv"]),
        ("total_large", "expenses_large.csv", gt_totals(B / "expenses_large.csv"),
         "Sum the `amount` column of expenses_large.csv (800 data rows) and count its rows.",
         '{"n_rows": <int>, "total_amount": <number>}', ["expenses_large.csv"]),
        ("top_vendor_large", "expenses_large.csv", gt_top_vendor(B / "expenses_large.csv"),
         "Find the `vendor` with the largest total `amount` in expenses_large.csv.",
         '{"vendor_with_largest_total": <string>, "largest_vendor_total": <number>}',
         ["expenses_large.csv"],
         lambda d: {"vendor_with_largest_total": d["top_vendor"],
                    "largest_vendor_total": d["top_vendor_total"]}),
        ("travel_across_files", None,
         (lambda d: {"travel_total_by_file": d["travel_by_file"], "travel_total": d["travel_total"]})(
             gt_selective(SELECTIVE, "travel")),
         "Across the four CSV files, sum the `amount` where `category` is 'travel', per file "
         "and overall.",
         '{"travel_total_by_file": {"<file_stem_without_.csv>": <number>}, "travel_total": <number>}',
         SELECTIVE),
    ]

    items = []
    for key, one, expected, task, shape, files, *remap in specs:
        if remap:
            expected = remap[0](expected)
        ctx = (f"File in the working directory: {', '.join(files)}.\n\n" if len(files) == 1
               else f"Files in the working directory: {', '.join(files)}.\n\n")
        for mode, lead, cls in (("inhead", INHEAD_ASK, "structured_extract"),
                                ("code", CODE_ASK, "compute_via_code")):
            body = f"{task}\n{CONVENTIONS}{lead}{shape}\n\n"
            if mode == "inhead":
                body += ctx + "\n\n".join(
                    f"=== {fn} ===\n" + (B / fn).read_text().strip() for fn in files)
            items.append({"id": f"b_{key}_{mode}", "task_stem": key, "arch": mode,
                          "prompt_class": cls, "files": files, "prompt": body,
                          "expected": expected})
    return items


def main():
    for fn in SOURCES:
        if not (BCORPUS / fn).exists():
            raise SystemExit(f"battery corpus missing {fn}; run make_battery.py first")
    items = build()
    out = HERE / "battery" / "items_v2.jsonl"
    out.write_text("".join(json.dumps(it, sort_keys=True) + "\n" for it in items))
    man = {"items": out.name, "n_items": len(items),
           "items_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
           "architecture_pairs": "each task exists as *_inhead (model computes) and *_code "
                                 "(model writes code, harness runs it) on identical ground truth",
           "corpus": {fn: hashlib.sha256((BCORPUS / fn).read_bytes()).hexdigest() for fn in SOURCES},
           "by_class": {}, "prompt_chars": {it["id"]: len(it["prompt"]) for it in items},
           "ground_truth": "computed by make_battery.py / this module from the copied corpus"}
    for it in items:
        man["by_class"][it["prompt_class"]] = man["by_class"].get(it["prompt_class"], 0) + 1
    (HERE / "battery" / "manifest_v2.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")

    print(f"frozen {len(items)} items -> {out.relative_to(L5)}")
    print(f"sha256 {man['items_sha256'][:16]}…  by class {json.dumps(man['by_class'], sort_keys=True)}")
    for it in items:
        print(f"  {it['id']:26} {it['prompt_class']:18} chars={len(it['prompt']):>6}")
    print("\nground truth per task:")
    for it in items:
        if it["arch"] == "inhead":
            print(f"  {it['task_stem']:22} {json.dumps(it['expected'])[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
