#!/usr/bin/env python3
"""run_reps.py — repeat the frozen battery N times over the live connect list.

Why this exists: every capability cell in the paper has n = 6, where one correct answer moves
accuracy by 0.17. Repeating the frozen battery bounds two things the single roll could not:
serving-side variance (the same prompt at temperature 0 is not guaranteed byte-identical) and
the sensitivity of a cell mean to a single item. It does **not** replace more items — repetitions
of the same twelve questions measure stability, not generality, and the paper says so.

Grading uses the output shape each prompt actually mandates (amendment B.1), not the stored key
names. No new battery is written; the frozen one is checksum-verified first.

    .venv/bin/python experiments/exp1/run_reps.py --reps 3 --dry-run   # cost first
    .venv/bin/python experiments/exp1/run_reps.py --reps 3
"""
import argparse, hashlib, json, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

import connections as X  # noqa: E402
import transport as TX  # noqa: E402
from code_exec import extract_code, run_code  # noqa: E402
from store import Store, new_id  # noqa: E402
import tokens as T  # noqa: E402

ARMS = ["llama3.2:latest", "llama3.1:8b-instruct-q4_K_M",
        "Together GLM-5.3-Flash", "Qwen3.8-2.4T-A95B"]
CEILING_USD = 3.00
# amendment B.1: the prompts mandate these names; the ground truth stores the other
ALIAS = {"total_amount": "total"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def score_field(expected, got):
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


def score(expected, text):
    try:
        parsed = json.loads((text or "").strip())
    except ValueError:
        return 0, len(expected), False
    if not isinstance(parsed, dict):
        return 0, len(expected), False
    got = {ALIAS.get(k, k): v for k, v in parsed.items()}
    ok = sum(1 for k, v in expected.items() if k in got and score_field(v, got.get(k)))
    return ok, len(expected), True


def grade(item, text):
    if item["arch"] == "inhead":
        c, t, schema = score(item["expected"], text)
        return {"fields_correct": c, "fields_total": t,
                "quality": round(c / t, 4) if t else None, "schema_compliance": schema,
                "code_ran": None, "program_stdout": None}
    code = extract_code(text)
    res = run_code(code, item["files"], HERE / "battery" / "corpus")
    c, t, schema = score(item["expected"],
                         json.dumps(res["parsed"]) if res["parsed"] is not None else "")
    return {"fields_correct": c, "fields_total": t,
            "quality": round(c / t, 4) if t else None, "schema_compliance": schema,
            "code_ran": bool(res["ran"]), "program_stdout": (res.get("stdout") or "")[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="exp1-reps-v1")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--arms", default=None,
                    help="comma-separated labels; default is the four standing arms")
    ap.add_argument("--budget", type=int, default=None,
                    help="override the output-token budget for every arm (budget probe)")
    a = ap.parse_args()

    battery = HERE / "battery" / "items_v2.jsonl"
    man = json.loads((HERE / "battery" / "manifest_v2.json").read_text())
    raw = battery.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["items_sha256"]:
        raise SystemExit(f"battery drift: {got[:16]} != {man['items_sha256'][:16]}")
    items = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]

    store = Store(L5)
    conns, problems = X.load(store, env_file=X.ENV_FILE)
    if problems:
        print("unusable connections (skipped):", [p["label"] for p in problems])
    by_label = {c["label"]: c for c in conns}
    want = ([x.strip() for x in a.arms.split(",")] if a.arms else ARMS)
    missing = [x for x in want if x not in by_label]
    if missing:
        raise SystemExit(f"missing connections: {missing}; have {sorted(by_label)}")
    arms = [by_label[x] for x in want]
    if a.budget:
        for c in arms:
            c["output_budget_tokens"] = a.budget

    est_total = 0.0
    print(f"battery v2: {len(items)} items, sha {got[:12]}…  arms={len(arms)} reps={a.reps}")
    for c in arms:
        tot = 0.0
        for it in items:
            n, method = T.count_or_bound(it["prompt"], c.get("tokenizer_ref"))
            if n + c["output_budget_tokens"] > 0.95 * c["context_window"]:
                continue
            tot += (n * c["price"]["input_per_mtok"]
                    + c["output_budget_tokens"] * c["price"]["output_per_mtok"]) / 1e6
        tot *= a.reps
        est_total += tot
        print(f"  {c['label']:32} ${tot:.4f}")
    print(f"  TOTAL ${est_total:.4f} (ceiling ${CEILING_USD})")
    if est_total > CEILING_USD:
        raise SystemExit("PRE-FLIGHT HALT: over ceiling, no call made.")
    if a.dry_run:
        print("dry run: nothing called")
        return 0

    rows, spent, t0 = [], 0.0, time.time()
    for it in items:
        for c in arms:
            n_in, method = T.count_or_bound(it["prompt"], c.get("tokenizer_ref"))
            room = 0.95 * c["context_window"]
            if n_in + c["output_budget_tokens"] > room:
                rows.append({"item": it["id"], "arch": it["arch"], "prompt_class": it["prompt_class"],
                             "arm": c["label"], "model_ref": c["id"], "status": "excluded_context",
                             "detail": f"need={n_in + c['output_budget_tokens']} room={room:.0f}",
                             "cost_usd": 0.0, "quality": None})
                continue
            for rep in range(1, a.reps + 1):
                res = TX.call(c, it["prompt"], c["output_budget_tokens"], sleep=lambda s: None)
                cost = 0.0
                if res["ok"] and res["tokens_in"] is not None:
                    cost = (res["tokens_in"] * c["price"]["input_per_mtok"]
                            + res["tokens_out"] * c["price"]["output_per_mtok"]) / 1e6
                    spent += cost
                g = grade(it, res.get("text") or "") if res["ok"] else {
                    "fields_correct": 0, "fields_total": len(it["expected"]), "quality": 0.0,
                    "schema_compliance": False, "code_ran": None, "program_stdout": None}
                rows.append({"item": it["id"], "arch": it["arch"],
                             "prompt_class": it["prompt_class"], "arm": c["label"],
                             "model_ref": c["id"], "rep": rep,
                             "status": "ok" if res["ok"] else res["error_class"],
                             "tokens_in": res.get("tokens_in"), "tokens_out": res.get("tokens_out"),
                             "finish_reason": res.get("finish_reason"),
                             "terminated": res.get("finish_reason") != "length",
                             "cost_usd": cost, "est_tokens_in": n_in,
                             "est_tokens_method": method, **g})
                print(f"  {it['id']:26} {c['label'][:22]:22} r{rep} "
                      f"{'ok ' if res['ok'] else res['error_class']:16} q={rows[-1]['quality']} "
                      f"${cost:.5f}")

    RAW = HERE / "results" / "raw"
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"reps_{a.run}.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    meta = {"run": a.run, "battery_sha256": got, "n_items": len(items), "reps": a.reps,
            "arms": [{"label": c["label"], "model": c["model"], "locus": c["locus"],
                      "price": c["price"]} for c in arms],
            "preflight_estimate_usd": round(est_total, 4),
            "ACTUAL_SPEND_USD": round(spent, 6),
            "wall_clock_s": round(time.time() - t0, 1),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "grader": "B.1-key-alias (in-run)"}
    (RAW / f"run_meta_{a.run}.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")

    # per-arm, per-class stability across repetitions
    print("\nper arm / class (mean over measurable rows, and the range across reps):")
    summary = {}
    for c in arms:
        for pcls in sorted({it["prompt_class"] for it in items}):
            rs = [r for r in rows if r["arm"] == c["label"] and r["prompt_class"] == pcls
                  and r.get("quality") is not None]
            if not rs:
                continue
            per_rep = {}
            for rep in range(1, a.reps + 1):
                xs = [r["quality"] for r in rs if r.get("rep") == rep]
                if xs:
                    per_rep[rep] = round(statistics.fmean(xs), 4)
            key = f"{c['label']}|{pcls}"
            summary[key] = {"n": len(rs), "mean_accuracy": round(statistics.fmean(
                r["quality"] for r in rs), 4), "per_rep": per_rep,
                "code_runs_rate": (round(statistics.fmean(
                    1.0 if r.get("code_ran") else 0.0 for r in rs), 4)
                    if pcls == "compute_via_code" else None),
                "cost_usd": round(sum(r["cost_usd"] for r in rs), 6)}
            v = summary[key]
            spread = (max(per_rep.values()) - min(per_rep.values())) if len(per_rep) > 1 else None
            print(f"  {c['label']:32} {pcls:20} n={v['n']:2} mean={v['mean_accuracy']} "
                  f"per-rep={per_rep} spread={spread} ${v['cost_usd']:.5f}")
    (RAW / f"summary_{a.run}.json").write_text(
        json.dumps({"spend_usd": round(spent, 6), "cells": summary}, indent=1, sort_keys=True) + "\n")
    print(f"\nspend ${spent:.6f}; raw -> {RAW.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
