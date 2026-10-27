#!/usr/bin/env python3
"""rules_audit.py — classify both privacy batteries with the ACTIVE ruleset and count errors by
direction. No model is called; this is the local check that should have been run before every
paid annotation round.

It writes `results/derived/rules_audit.json`, which the spine checks. The number to watch is
**under-classifications**: a prompt about a person that the router files as public or
confidential. Over-classification is the cost of failing closed and is reported, not hidden.
"""
import json, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

import classify as C  # noqa: E402
from store import Store  # noqa: E402

ORDER = {"public": 0, "confidential": 1, "private": 2}


def main():
    store = Store(L5)
    act = store.records("policy", status="active")
    pol = ([p for p in act if p.get("is_default") is True]
           or sorted(act, key=lambda p: p["created_at"]))[-1]
    rules = store.get("rules", pol["classifier"]["privacy_rules_ref"])
    print(f"policy {pol['version_label']} -> rules {rules.get('version_label')} ({rules['id']})")

    rows = []
    for name in ("prompts.jsonl", "prompts_expansion.jsonl", "prompts_heldout.jsonl",
                 "prompts_heldout_b.jsonl"):
        for line in (HERE / "battery" / name).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            got, rank, matched, guard = C.classify({"text": r["text"]}, rules)
            rows.append({"id": r["id"], "set": name, "truth": r["label"], "rule_class": got,
                         "rank": rank, "defaulted": rank is None,
                         "guarded": bool(guard), "guard": guard})

    def summarise(rs):
        under = [r["id"] for r in rs if ORDER[r["rule_class"]] < ORDER[r["truth"]]]
        over = [r["id"] for r in rs if ORDER[r["rule_class"]] > ORDER[r["truth"]]]
        ok = sum(1 for r in rs if r["rule_class"] == r["truth"])
        return {"n": len(rs), "accuracy": round(ok / len(rs), 4) if rs else None,
                "under": under, "over": over,
                "defaulted": sorted(r["id"] for r in rs if r["defaulted"])}

    # the held-out battery was written after the ruleset was frozen and is reported on its own:
    # it must never be pooled with the set the rules were revised against
    DEV = "prompts_heldout.jsonl"        # battery A: used to design the guard
    EVAL = "prompts_heldout_b.jsonl"     # battery B: written after the guard, never tuned on
    tuning = [r for r in rows if r["set"] not in (DEV, EVAL)]
    out = {"policy": pol["id"], "policy_label": pol["version_label"],
           "rules": rules["id"], "rules_label": rules.get("version_label"),
           "all": summarise(tuning),
           "dev_a": summarise([r for r in rows if r["set"] == DEV]),
           "heldout": summarise([r for r in rows if r["set"] == EVAL]),
           "by_set": {s: summarise([r for r in rows if r["set"] == s])
                      for s in sorted({r["set"] for r in rows})},
           "by_class": {c: dict(Counter(r["rule_class"] for r in rows if r["truth"] == c))
                        for c in ("public", "confidential", "private")},
           "rows": rows}
    d = HERE / "results" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rules_audit.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    for k, v in out["by_set"].items():
        print(f"  {k:28} n={v['n']:3} acc={v['accuracy']} under={len(v['under'])} "
              f"over={len(v['over'])} defaulted={len(v['defaulted'])}")
    print(f"  TUNING SET                   n={out['all']['n']:3} acc={out['all']['accuracy']} "
          f"under={len(out['all']['under'])} over={len(out['all']['over'])}")
    for label, key in (("DEV (battery A)", "dev_a"), ("HELD-OUT (battery B)", "heldout")):
        h = out[key]
        print(f"  {label:28} n={h['n']:3} acc={h['accuracy']} under={len(h['under'])} "
              f"over={len(h['over'])} defaulted={len(h['defaulted'])}")
        if h["under"]:
            print(f"  {label} UNDER-CLASSIFIED:", h["under"])
    print(f"frozen: {(d / 'rules_audit.json').relative_to(L5)}")
    return 0 if not out["all"]["under"] else 1


if __name__ == "__main__":
    sys.exit(main())
