#!/usr/bin/env python3
"""Experiment 1 pilot: cost-quality frontier. Pre-registration in FREEZE.md.

Scoring is exact-match against ground truth COMPUTED from the corpus by make_battery.py —
no LLM judge, no human. Costs are the provider's own metered token counts times the frozen
registry price, never an estimate.

Pre-flight: the run estimates its own cost from the frozen prices BEFORE the first call and
halts above a hard ceiling, so the pilot cannot overrun.
"""
import hashlib, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

import cost as K  # noqa: E402
import tokens as T  # noqa: E402
def make_backends():  # noqa: E402
    raise SystemExit("experiment 1 is frozen: its harness used the per-provider backends that "
                     "RC-2 deleted. The roll, its artifacts and its numbers remain; re-running "
                     "it would need a rewrite against the connect list.")
from decide import Router  # noqa: E402
from store import Store  # noqa: E402

PILOT_CEILING_USD = 1.50
RUN = "exp1-pilot"
HAZARD_ITEMS = {"b_total_large"}          # the reasoning arm runs on 1 item only: it is
                                          # the most expensive arm and the hazard is what it
                                          # measures, so breadth adds nothing
ARMS = [
    ("always-small", "llama3.2:latest", "chat"),
    ("always-small-plus", "llama3.1:8b-instruct-q4_K_M", "chat"),
    ("always-big", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "chat"),
    ("always-big-max", "Qwen/Qwen3.8-2.4T-A95B", "chat"),
    ("hazard-reasoning", "Qwen/Qwen3.8-2.4T-A95B", "reasoning"),
]


def load_battery():
    man = json.loads((HERE / "battery" / "manifest.json").read_text())
    raw = (HERE / "battery" / "items.jsonl").read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["items_sha256"]:
        raise SystemExit(f"BATTERY DRIFT: frozen {man['items_sha256'][:16]}… got {got[:16]}…")
    items = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    for fn, v in man["corpus"].items():
        p = HERE / "battery" / "corpus" / fn
        if hashlib.sha256(p.read_bytes()).hexdigest() != v["sha256"]:
            raise SystemExit(f"corpus drift: {fn}")
    return items, man


def num(x):
    try:
        return round(float(x), 2)
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


def score(expected, reply_text):
    """Returns (fields_correct, fields_total, schema_ok, parsed)."""
    text = (reply_text or "").strip()
    schema_ok = False
    parsed = None
    try:
        parsed = json.loads(text)
        schema_ok = isinstance(parsed, dict)
    except ValueError:
        pass
    if not schema_ok or not isinstance(parsed, dict):
        return 0, len(expected), False, None
    ok = sum(1 for k, v in expected.items() if k in parsed and score_field(v, parsed.get(k)))
    return ok, len(expected), True, parsed


def variant_for(store, upstream_id, mode):
    for m in store.records("models", status="active"):
        if m["upstream_id"] == upstream_id and m["mode"] == mode:
            return m
    raise SystemExit(f"no active variant for {upstream_id} mode={mode}")


def main():
    store = Store(L5)
    items, man = load_battery()
    pol = Router(store=store, free_ram_gb=12.0).policy()
    print(f"battery: {len(items)} items sha {man['items_sha256'][:12]}…  policy {pol['version_label']}")

    variants = {name: variant_for(store, up, mode) for name, up, mode in ARMS}

    # ---- pre-flight cost estimate, before any call ----
    est_total = 0.0
    print("\npre-flight estimate (frozen prices x per-variant tokenizer, output at full budget):")
    ests = {}
    for name, m in variants.items():
        tot = 0.0
        for it in items:
            if name == "hazard-reasoning" and it["id"] not in HAZARD_ITEMS:
                continue
            tin = T.count(it["prompt"], m["tokenizer_ref"])
            tot += (tin * m["price"]["input_per_mtok"]
                    + m["output_budget_tokens"] * m["price"]["output_per_mtok"]) / 1e6
        ests[name] = tot
        est_total += tot
        print(f"  {name:20} ${tot:.4f}")
    print(f"  TOTAL ESTIMATE       ${est_total:.4f}  (ceiling ${PILOT_CEILING_USD})")
    if est_total > PILOT_CEILING_USD:
        raise SystemExit("PRE-FLIGHT HALT: estimate exceeds the pilot ceiling. No call was made.")

    be = make_backends()
    RAW = HERE / "results" / "raw"
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    spent = 0.0
    for it in items:
        for name, m in variants.items():
            if name == "hazard-reasoning" and it["id"] not in HAZARD_ITEMS:
                continue
            est_in = T.count(it["prompt"], m["tokenizer_ref"])
            # A local arm given more context than its configured num_ctx is SILENTLY
            # truncated by Ollama, and a truncated input scored as a quality failure would
            # be a lie. These items are recorded as not-runnable-at-this-window, not wrong.
            room = pol["gates"]["context_fit"]["context_margin"] * m["context_window"]
            if est_in + m["output_budget_tokens"] > room:
                rows.append({"arm": name, "item": it["id"], "prompt_class": it["prompt_class"],
                             "model_ref": m["id"], "model": m["name"], "backend": m["backend"],
                             "locus": m["locus"], "est_tokens_in": est_in,
                             "status": "excluded_context",
                             "detail": f"need={est_in + m['output_budget_tokens']} > room={room:.0f}",
                             "quality": None, "cost_usd": 0.0, "run": RUN})
                print(f"  {it['id']:20} {name:19} SKIPPED (context: {rows[-1]['detail']})")
                continue
            t0 = time.time()
            try:
                res = be[m["backend"]].complete(m, it["prompt"])
            except Exception as e:
                rows.append({"arm": name, "item": it["id"], "model_ref": m["id"],
                             "error": str(e)[:250], "quality": None})
                print(f"  {it['id']:20} {name:19} ERROR {str(e)[:90]}")
                continue
            cost_usd = K.actual_cost_usd(m, res["tokens_in"], res["tokens_out"])
            spent += cost_usd
            correct, total, schema_ok, _ = score(it["expected"], res["text"])
            row = {"arm": name, "item": it["id"], "prompt_class": it["prompt_class"],
                   "model_ref": m["id"], "model": m["name"], "backend": m["backend"],
                   "locus": m["locus"], "est_tokens_in": est_in,
                   "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                   "finish_reason": res["finish_reason"],
                   "terminated": res["finish_reason"] != "length",
                   "schema_compliance": schema_ok,
                   "fields_correct": correct, "fields_total": total,
                   "quality": round(correct / total, 4) if total else None,
                   "cost_usd": cost_usd, "wall_clock_s": res["wall_clock_s"],
                   "answer": res["text"][:400], "run": RUN}
            rows.append(row)
            print(f"  {it['id']:20} {name:19} q={row['quality']} schema={int(schema_ok)} "
                  f"term={int(row['terminated'])} in={res['tokens_in']:>6} "
                  f"out={res['tokens_out']:>5} ${cost_usd:.5f}")

    # ---- the routed arm: a real routing decision, logged and bound ----
    router = Router(store=store, backends=be, free_ram_gb=12.0)
    routed = []
    for it in items:
        req = {"request_id": f"req_{RUN}_{it['id']}", "text": it["prompt"],
               "attachments": [{"type": "csv", "sha256": None, "bytes": 0}]}
        d = router.route(req, run=RUN)
        rec = store.get("decision_log", d["decision_ref"])
        m = store.get("models", (rec.get("chosen") or {}).get("model_ref", "")) if rec.get("chosen") else None
        att = (rec["attempts"] or [{}])[-1]
        corr, tot, schema_ok, _ = score(it["expected"], d["answer"])
        routed.append({"item": it["id"], "outcome": d["outcome"],
                       "privacy_class": rec["privacy_class"], "task_class": rec["task_class"],
                       "model": m["name"] if m else None, "locus": m["locus"] if m else None,
                       "attempts": len(rec["attempts"] or []),
                       "tokens_in": att.get("tokens_in"), "tokens_out": att.get("tokens_out"),
                       "cost_usd": att.get("actual_cost_usd", 0.0),
                       "fields_correct": corr, "fields_total": tot,
                       "quality": round(corr / tot, 4) if tot else None,
                       "schema_compliance": schema_ok,
                       "decision_ref": rec["id"], "binding_ref": rec["binding_ref"]})
        spent += att.get("actual_cost_usd", 0.0) or 0.0
        print(f"  ROUTED {it['id']:20} -> {routed[-1]['model']} "
              f"({routed[-1]['locus']}) outcome={d['outcome']} ${routed[-1]['cost_usd']:.5f}")

    (RAW / f"forced_arms_{RUN}.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    (RAW / f"routed_arm_{RUN}.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in routed))
    (RAW / f"run_meta_{RUN}.json").write_text(json.dumps({
        "run": RUN, "battery_sha256": man["items_sha256"], "policy_id": pol["id"],
        "policy_label": pol["version_label"], "reps": 1, "temperature": 0,
        "preflight_estimate_usd": round(est_total, 6), "preflight_by_arm": {k: round(v, 6) for k, v in ests.items()},
        "ACTUAL_SPEND_USD": round(spent, 6), "ceiling_usd": PILOT_CEILING_USD,
        "n_forced_calls": len(rows), "n_routed": len(routed),
        "note": "forced arms are experiment controls and are NOT written to ds:decision_log "
                "(FREEZE §3); the routed arm is logged and bound",
    }, indent=1, sort_keys=True) + "\n")
    print(f"\nACTUAL TOTAL SPEND: ${spent:.6f}  (pre-flight estimate ${est_total:.4f}; "
          f"ceiling ${PILOT_CEILING_USD})")
    print(f"raw: {RAW.relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
