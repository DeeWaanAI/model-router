#!/usr/bin/env python3
"""Amendment B.1 regrade: score the frozen exp1 outputs against the shape the prompts mandated.

What happened. Battery v2 tells every model to print `{"n_rows": <int>, "total_amount": <number>}`
and stores the ground truth as `{"n_rows": ..., "total": ...}`. The scorer looks up fields BY NAME,
so a program that ran correctly and printed the exactly right sum was scored half-wrong on the four
`b_total_*` items. This is a defect in Layer 5's own instrument, not a model behaviour: re-summing
the frozen corpus by hand gives 800 rows / 361,572.40 and 50 rows / 25,508.69, and four of the five
arms printed precisely those numbers under the key they were told to use.

Why a regrade and not a new roll. The prompt text is unchanged — the models were asked the same
question and the raw outputs are already on disk, so the alias corrects the interpretation of
evidence that exists. A fresh roll would spend money to measure sampling noise around a scoring bug.
Zero new model calls, zero new spend.

What this does NOT do:
* it does not touch the frozen raw artifact; it writes a corrected sidecar alongside it;
* it cannot regrade the two `b_total_*_inhead` items, because run_v2.py stored only the score for
  in-head replies, not the reply text. Those rows carry their original score and are labelled
  `not_regradable`. The in-head class is `structured_extract`, which no routing decision in exp3
  depends on — but the gap is recorded rather than smoothed over;
* it changes only rows whose item has a declared-key/expected-key mismatch. The script asserts that
  no other row moved, so a fix for one bug cannot silently become a change to another measurement.

The corrected rows still need to enter ds:capabilities as a NEW measurement with its own provenance
(spec §9.1 succession); this script does not write the dataset.
"""
import hashlib, json, re, statistics, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent

RUN = "exp1-pilot-v3"
RAW = HERE / "results" / "raw" / f"arms_{RUN}.jsonl"
BATTERY = HERE / "battery" / "items_v2.jsonl"
MANIFEST = HERE / "battery" / "manifest_v2.json"
DECLARED = re.compile(r"exactly this shape: (\{.*\})")

fails = []


def declared_keys(prompt):
    """Top-level keys the PROMPT mandates. `<int>`, `<number>` and `<file_stem_without_.csv>` are
    placeholders, so they become null and the shape is parsed as real JSON. A non-greedy brace
    match would truncate a nested shape and invent a mismatch — that false alarm is what this
    function exists to prevent."""
    m = DECLARED.search(prompt)
    if not m:
        return None
    try:
        obj = json.loads(re.sub(r"<[^>]*>", "null", m.group(1)))
    except ValueError:
        return None
    return list(obj) if isinstance(obj, dict) else None


def truth(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def score_field(expected, got):
    """Same rule as run.py:score_field — copied, not imported, so a future edit to the frozen
    scorer cannot silently change what this regrade means."""
    if isinstance(expected, bool) or isinstance(expected, (int, float)):
        e, g = num(expected), num(got)
        return e is not None and g is not None and abs(e - g) <= max(0.01, abs(e) * 1e-6)
    if isinstance(expected, dict):
        if not isinstance(got, dict) or set(expected) != set(got):
            return False
        return all(score_field(v, got[k]) for k, v in expected.items())
    if isinstance(expected, str):
        return str(got).strip().lower() == expected.strip().lower()
    return expected == got


def main():
    man = json.loads(MANIFEST.read_text())
    bat_bytes = BATTERY.read_bytes()
    got_sha = hashlib.sha256(bat_bytes).hexdigest()
    truth("battery v2 is the frozen one", got_sha == man["items_sha256"], got_sha[:16])
    items = {it["id"]: it for it in
             (json.loads(l) for l in bat_bytes.decode().splitlines() if l.strip())}

    # 1. find every prompt/ground-truth key mismatch mechanically — no hand-picked alias list
    aliases, mismatches, unparsed = {}, [], []
    for iid, it in sorted(items.items()):
        declared = declared_keys(it["prompt"])
        if declared is None:
            unparsed.append(iid)
            continue
        truth_keys = list(it["expected"])
        if sorted(declared) != sorted(truth_keys):
            mismatches.append({"item": iid, "declared": declared, "expected": truth_keys})
            if len(declared) == len(truth_keys):
                # position-for-position rename: the prompt's k-th key carries the truth's k-th value
                for d, t in zip(declared, truth_keys):
                    if d != t:
                        aliases.setdefault(d, t)
    print(f"key mismatches found mechanically: {len(mismatches)} items -> alias map {aliases}")
    if unparsed:
        print(f"  shapes not parseable (excluded from the check, not assumed clean): {unparsed}")
    for mm in mismatches:
        print(f"  {mm['item']:28} prompt says {mm['declared']}, ground truth says {mm['expected']}")
    truth("every mismatch is a pure rename (same arity), so the alias is unambiguous",
          all(len(m["declared"]) == len(m["expected"]) for m in mismatches),
          [m for m in mismatches if len(m["declared"]) != len(m["expected"])])

    # 2. regrade from the frozen artifact
    mismatch_ids = {m["item"] for m in mismatches}
    rows = [json.loads(l) for l in RAW.read_text().splitlines() if l.strip()]
    out_rows, changed, not_regradable, untouched_unchanged = [], 0, [], 0
    for r in rows:
        it = items.get(r["item"])
        nr = dict(r)
        nr["grader"] = {"version": "B.1-key-alias", "source": RAW.name,
                        "aliases": aliases if it and declared_keys(it["prompt"]) else {}}
        if it is None:
            nr["regrade_status"] = "unknown_item"
            not_regradable.append(r["item"])
        elif it["arch"] == "inhead":
            # the reply text was never stored for in-head rows; the score is not recomputable
            nr["regrade_status"] = "not_regradable_no_reply_text"
            not_regradable.append(f"{r['item']}/{r['arm']}")
        elif r.get("quality") is None:
            nr["regrade_status"] = "not_graded_originally"
        else:
            try:
                parsed = json.loads(r.get("program_stdout") or "")
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                nr["regrade_status"] = "no_parsable_stdout"
            else:
                exp = {aliases.get(k, k): v for k, v in parsed.items()}
                c = sum(1 for k, v in it["expected"].items()
                        if k in exp and score_field(v, exp.get(k)))
                t = len(it["expected"])
                nr.update({"fields_correct": c, "fields_total": t,
                           "quality": round(c / t, 4) if t else None,
                           "regrade_status": "regraded"})
                if c != r["fields_correct"]:
                    changed += 1
                if r["item"] not in mismatch_ids and c != r["fields_correct"]:
                    fails.append(f"row on a key-matched item moved: {r['item']}/{r['arm']}")
                elif r["item"] not in mismatch_ids:
                    untouched_unchanged += 1
        out_rows.append(nr)

    print(f"\n{len(out_rows)} rows read; {changed} scores corrected; "
          f"{sum(1 for r in out_rows if r['regrade_status'] == 'regraded')} rows regraded from stdout; "
          f"{sum(1 for r in out_rows if r['regrade_status'] == 'regraded') - changed} of them "
          f"unchanged; {len(not_regradable)} in-head/unknown rows carried over with no reply text")
    truth("no row on a key-matched item changed score (the fix cannot leak)",
          not [f for f in fails if "key-matched item moved" in f], fails)

    # 3. aggregates, before and after
    def agg(rs):
        q = [r["quality"] for r in rs if r.get("quality") is not None]
        return {"n": len(q), "accuracy": round(statistics.fmean(q), 4) if q else None,
                "fields_correct": sum(r["fields_correct"] for r in rs if r.get("quality") is not None),
                "fields_total": sum(r["fields_total"] for r in rs if r.get("quality") is not None)}

    before, after = defaultdict(list), defaultdict(list)
    models = {m["id"]: m["name"] for m in
              (json.loads(l) for l in (L5 / "datasets/models/data.jsonl").read_text().splitlines()
               if l.strip())}
    for r, nr in zip(rows, out_rows):
        name = models.get(r.get("model_ref") or "") or r.get("model") or r.get("status", "unknown")
        k = (name, r["prompt_class"])
        before[k].append(r)
        after[k].append(nr)

    print("\nper-arm, per-class accuracy (frozen grader -> B.1 grader):")
    table = []
    for k in sorted(after):
        b, a = agg(before[k]), agg(after[k])
        table.append({"model": k[0], "prompt_class": k[1], "before": b, "after": a})
        print(f"  {k[0]:30} {k[1]:20} {b['accuracy']} -> {a['accuracy']}  "
              f"({a['fields_correct']}/{a['fields_total']} fields)")

    # the H3 question, answered with the corrected scorer: the 800-row total
    big = [r for r in out_rows if r["item"] == "b_total_large_code"]
    got_right = [r.get("model") for r in big if r.get("quality") == 1.0]
    print(f"\n  b_total_large_code (800-row sum, via code): arms at full credit = {got_right}")
    truth("the corrected grader credits the arms whose program printed the right total",
          len(got_right) >= 3, len(got_right))

    # 4. freeze: sidecar raw file + summary, neither touching the original
    d = HERE / "results" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    side = d / f"arms_{RUN}-regraded.jsonl"
    side.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out_rows))

    # The routed arm is the headline number, and it is graded by the same scorer, so it gets the
    # same treatment. It is a separate file because it is a separate measurement: one row per
    # item, chosen by the router, with its own decision reference.
    routed_rows = [json.loads(l) for l in
                   (HERE / "results" / "raw" / f"routed_{RUN}.jsonl").read_text().splitlines()
                   if l.strip()]
    out_routed, r_changed = [], 0
    for r in routed_rows:
        it = items.get(r["item"])
        nr = dict(r)
        nr["grader"] = {"version": "B.1-key-alias", "source": f"routed_{RUN}.jsonl",
                        "aliases": aliases}
        parsed = None
        try:
            parsed = json.loads(r.get("program_stdout") or "")
        except ValueError:
            parsed = None
        if it is None or it["arch"] != "code" or not isinstance(parsed, dict):
            nr["regrade_status"] = ("not_regradable_no_reply_text" if it and it["arch"] == "inhead"
                                    else "no_parsable_stdout")
        else:
            exp = {aliases.get(k, k): v for k, v in parsed.items()}
            c = sum(1 for k, v in it["expected"].items() if k in exp and score_field(v, exp.get(k)))
            t = len(it["expected"])
            nr.update({"fields_correct": c, "fields_total": t, "quality": round(c / t, 4) if t else None,
                       "regrade_status": "regraded"})
            if c != r["fields_correct"]:
                r_changed += 1
        out_routed.append(nr)
    rside = d / f"routed_{RUN}-regraded.jsonl"
    rside.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out_routed))
    rq = [r["quality"] for r in out_routed if r.get("quality") is not None]
    routed_after = {"n_graded": len(rq),
                    "fields_correct": sum(r["fields_correct"] for r in out_routed
                                          if r.get("quality") is not None),
                    "fields_total": sum(r["fields_total"] for r in out_routed
                                        if r.get("quality") is not None),
                    "scores_corrected": r_changed}
    routed_after["quality"] = (round(routed_after["fields_correct"] / routed_after["fields_total"], 4)
                               if routed_after["fields_total"] else None)
    print(f"\nrouted arm: {r_changed} of {len(out_routed)} rows rescored -> "
          f"quality {routed_after['quality']} "
          f"({routed_after['fields_correct']}/{routed_after['fields_total']} fields)")
    summary = {"amendment": "B.1", "run": RUN, "cost_usd": 0.0,
               "battery_sha256": got_sha,
               "defect": "prompt mandated key names differ from ground-truth key names",
               "affected_items": sorted({m["item"] for m in mismatches}),
               "alias_map": aliases,
               "n_rows": len(out_rows), "n_scores_corrected": changed,
               "n_not_regradable": len(not_regradable),
               "not_regradable_reason": "run_v2.py stored in-head scores but not in-head replies",
               "arms": table,
               "routed_arm": routed_after,
               "sidecar": str(side.relative_to(L5)),
               "routed_sidecar": str(rside.relative_to(L5))}
    (d / f"regrade_{RUN}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"\nfrozen: {side.relative_to(L5)}")
    print(f"frozen: {(d / f'regrade_{RUN}.json').relative_to(L5)}")
    if fails:
        print(f"\nREGRADE FAILED: {fails}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
