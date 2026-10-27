#!/usr/bin/env python3
"""verifier_eval.py — freeze the classifier-options table (paper §5.4, Table "classifier")
from raw per-item verdicts, on battery B (prompts_heldout_b.jsonl, 72 items, author labels).

The four measured options:
  a) rules            — the shipped ruleset (v6, referenced by the active policy) alone
  b) two_markers      — rules, but a public verdict stands only if TWO distinct public rules
                        match; otherwise demote to confidential (spec §19)
  c) local8b          — rules + verifier.check via the local llama3.1:8b endpoint
  d) frontier         — rules + verifier.check via Together Qwen3.8-2.4T-A95B (costs money)

Only PUBLIC verdicts are ever verified (the only direction that can under-classify), via
verifier.apply's contract: stricter wins, failed verification demotes to confidential.

Usage: verifier_eval.py --variant rules|two_markers|local8b|frontier
Writes one raw artifact per variant: results/raw/verifier_eval_<variant>_<ts>.json
"""
import argparse, hashlib, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

import classify, transport, verifier  # noqa: E402
from connections import load_env_file  # noqa: E402
from store import Store  # noqa: E402

load_env_file(L5 / ".env")  # explicit path; values never logged (HANDOFF §7)

ORDER = {"public": 0, "confidential": 1, "private": 2}

LOCAL8B = {"label": "local llama3.1:8b", "connector": "ollama",
           "base_url": "http://127.0.0.1:11434", "model": "llama3.1:8b-instruct-q4_K_M",
           "auth": {"scheme": "none", "key_env": None},
           "price": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}, "timeout_s": 120.0,
           "connect_timeout_s": 10.0, "context_window": 8192}
FRONTIER = {"label": "Together Qwen3.8", "connector": "openai_compatible",
            "base_url": "https://api.together.xyz/v1", "model": "Qwen/Qwen3.8-2.4T-A95B",
            "auth": {"scheme": "bearer", "key_env": "TOGETHER_AI_API_KEY"},
            "price": {"input_per_mtok": 2.0, "output_per_mtok": 6.0}, "timeout_s": 90.0,
            "connect_timeout_s": 10.0, "context_window": 131072}


def two_marker_label(request, ruleset):
    """Public stands only with >= 2 distinct public-rule matches; else confidential."""
    label, rank, skipped, guard = classify.classify(request, ruleset)
    if label != "public":
        return label, {"rank": rank, "guard": guard}
    text = request.get("text") or ""
    hits = 0
    for _, rule in classify._sorted_rules(ruleset):
        if rule.get("label") != "public":
            continue
        try:
            if classify._matches(rule.get("match") or {}, text, [], []):
                hits += 1
        except Exception:
            continue
    if guard:  # a guard already demoted this verdict inside classify()
        return label, {"rank": rank, "guard": guard, "public_hits": hits}
    return (label if hits >= 2 else "confidential"), {"rank": rank, "public_hits": hits}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    choices=["rules", "two_markers", "local8b", "frontier", "frontier_annot"])
    a = ap.parse_args()

    battery_path = HERE / "battery" / "prompts_heldout_b.jsonl"
    raw = battery_path.read_bytes()
    items = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    store = Store(L5)
    # resolve the ruleset exactly as experiments/exp2/rules_audit.py does
    act = store.records("policy", status="active")
    pol = ([p for p in act if p.get("is_default") is True]
           or sorted(act, key=lambda p: p["created_at"]))[-1]
    ruleset = store.get("rules", pol["classifier"]["privacy_rules_ref"])

    conn = {"local8b": LOCAL8B, "frontier": FRONTIER, "frontier_annot": FRONTIER}.get(a.variant)
    if conn and conn["auth"]["scheme"] != "none":
        # mirror what connection validation does in production: resolve key_env once, keep
        # the resolved key on the connection object, never in a record or log
        import os
        conn = dict(conn)
        conn["_key"] = os.environ[conn["auth"]["key_env"]]
    rows, cost = [], 0.0
    for it in items:
        req = {"text": it["text"]}
        rule_label, rank, _, guard = classify.classify(req, ruleset)
        rec = {"id": it["id"], "true": it["label"], "rules_label": rule_label,
               "guard_fired": bool(guard)}
        if a.variant == "rules":
            rec["final"] = rule_label
        elif a.variant == "two_markers":
            rec["final"], rec["detail"] = two_marker_label(req, ruleset)
        elif a.variant == "frontier_annot":
            # replacement semantics: the frontier model labels EVERY prompt (what the
            # original 0.931/3/2 row must have measured — it is unreachable under the
            # shipped verify-public-only contract, which can never reduce over-classification)
            verdict = verifier.check(transport, conn, it["text"])
            if verdict.get("cost_usd"):
                cost += verdict["cost_usd"]
            rec["verdict"] = {k: verdict.get(k) for k in
                              ("label", "error", "cost_usd", "tokens_in", "tokens_out")}
            rec["final"] = verdict["label"] or "confidential"
            rec["changed"] = rec["final"] != rule_label
        else:
            verdict = None
            if rule_label == "public":
                verdict = verifier.check(transport, conn, it["text"])
                if verdict.get("cost_usd"):
                    cost += verdict["cost_usd"]
                rec["verdict"] = {k: verdict.get(k) for k in
                                  ("label", "error", "cost_usd", "tokens_in", "tokens_out")}
            rec["final"], rec["changed"] = verifier.apply(rule_label, verdict)
        rows.append(rec)
        print(f"  {it['id']}: true={it['label']:12} rules={rule_label:12} final={rec['final']}")

    n = len(rows)
    correct = sum(1 for r in rows if r["final"] == r["true"])
    under = [r["id"] for r in rows if ORDER[r["final"]] < ORDER[r["true"]]]
    over = [r["id"] for r in rows if ORDER[r["final"]] > ORDER[r["true"]]]
    doc = {
        "experiment": "classifier options on battery B (held-out, unseen domains)",
        "script": "experiments/exp2/verifier_eval.py",
        "variant": a.variant,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "battery": {"file": "prompts_heldout_b.jsonl", "n": n,
                    "sha256": hashlib.sha256(raw).hexdigest()},
        "ruleset_ref": ruleset["id"],
        "verifier_conn": conn and {k: conn[k] for k in ("label", "connector", "model")},
        "metrics": {"accuracy": round(correct / n, 4), "n": n,
                    "under": len(under), "over": len(over),
                    "under_ids": under, "over_ids": over},
        "spend_usd": round(cost, 6),
        "rows": rows,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / "raw" / f"verifier_eval_{a.variant}_{ts}.json"
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"\n{a.variant}: acc={doc['metrics']['accuracy']} under={len(under)} "
          f"over={len(over)} spend=${cost:.4f}\nfrozen -> {out}")


if __name__ == "__main__":
    main()
