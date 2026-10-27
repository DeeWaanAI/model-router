#!/usr/bin/env python3
"""annotate.py — a second opinion on the privacy labels, and a larger set to have it on.

Two things the 44-prompt battery could not give us: a tighter estimate (44 items put a ±7.5
point interval around an accuracy of 0.93) and any measure of whether the labels are
*labellable* by someone else. This script adds an 80-item expansion and asks an independent
annotator — a frontier model that has never seen our rules — to label all 124 items. It then
reports three pairwise agreements with Cohen's kappa: rules vs author, annotator vs author,
rules vs annotator.

What this is and is not, stated before the numbers: the annotator is a **model, not a human**.
It measures whether the task is well-posed enough for an independent rater to reproduce the
labels, not whether a second person would. The battery is synthetic; no real personal data is
sent to the annotator, and the paper says so explicitly because sending private-looking text to
a cloud model is precisely the trade-off this layer exists to make visible.

    .venv/bin/python experiments/exp2/annotate.py [--limit N]
"""
import argparse, json, statistics, sys, time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent.parent
sys.path.insert(0, str(L5 / "router"))

import classify as C  # noqa: E402
import connections as X  # noqa: E402
import transport as TX  # noqa: E402
from store import Store  # noqa: E402

CLASSES = ["public", "confidential", "private"]
ANNOTATOR_LABEL = "Qwen3.8-2.4T-A95B"
RUBRIC = """You label text for a data-handling policy. Return ONLY one JSON object:
{"class": "public"} or {"class": "confidential"} or {"class": "private"}.

Definitions:
- private: personal data about an identifiable individual - health, finances, legal matters,
  employment matters, credentials or secrets, identifying numbers, family, location.
- confidential: internal business data that is not personal - customers, clients, contracts,
  internal plans, board material, unreleased work.
- public: no personal and no internal data - general knowledge, published material, public
  technical questions, generic advice.
If a text contains both personal and business data, choose private.
If it contains both public and sensitive material, choose the sensitive one.

Text to label:
"""


def kappa(pairs):
    """Cohen's kappa over (rater_a, rater_b) labels."""
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in CLASSES)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else None


def confusion(pairs):
    m = defaultdict(Counter)
    for truth, got in pairs:
        m[truth][got] += 1
    return {t: dict(c) for t, c in sorted(m.items())}


def accuracy(pairs):
    return round(sum(1 for a, b in pairs if a == b) / len(pairs), 4) if pairs else None


def annotate(conn, text):
    res = TX.call(conn, RUBRIC + text, 512, sleep=lambda s: None)
    cost = 0.0
    if res["ok"] and res.get("tokens_in") is not None:
        cost = (res["tokens_in"] * conn["price"]["input_per_mtok"]
                + res["tokens_out"] * conn["price"]["output_per_mtok"]) / 1e6
    if not res["ok"]:
        return None, res["error_class"], cost
    raw = (res.get("text") or "").strip()
    start = raw.find("{")
    if start < 0:
        return None, "no_json", cost
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    except ValueError:
        return None, "bad_json", cost
    label = str(obj.get("class", "")).lower()
    return ((label if label in CLASSES else None),
            ("ok" if label in CLASSES else "bad_class"), cost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--annotator-model", default=None,
                    help="ephemeral Together model id (different family); default uses the "
                         "Qwen connection from the connect list")
    ap.add_argument("--price-in", type=float, default=0.30)
    ap.add_argument("--price-out", type=float, default=1.20)
    ap.add_argument("--include-heldout", action="store_true")
    ap.add_argument("--out", default="annotation_agreement")
    ap.add_argument("--annotator-connection", default=None,
                    help="label of a connection in ds:models (e.g. a local model)")
    ap.add_argument("--only-set", default=None, help="restrict to one battery file")
    a = ap.parse_args()

    items = []
    names = ["prompts.jsonl", "prompts_expansion.jsonl"]
    if a.include_heldout:
        names += ["prompts_heldout.jsonl", "prompts_heldout_b.jsonl"]
    if a.only_set:
        names = [a.only_set]
    for name in names:
        p = HERE / "battery" / name
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                items.append({"id": r["id"], "text": r["text"], "truth": r["label"],
                              "set": name})
    if a.limit:
        items = items[:a.limit]

    store = Store(L5)
    act = store.records("policy", status="active")
    pol = ([p for p in act if p.get("is_default") is True]
           or sorted(act, key=lambda p: p["created_at"]))[-1]
    rules = store.get("rules", pol["classifier"]["privacy_rules_ref"])

    conns, problems = X.load(store, env_file=X.ENV_FILE)
    if a.annotator_model:
        # an ephemeral connection: a different model family, not added to the user's list
        conn = {"id": "mdl_ephemeral", "label": f"annotator {a.annotator_model}",
                "connector": "openai_compatible", "base_url": "https://api.together.xyz/v1",
                "model": a.annotator_model,
                "auth": {"scheme": "bearer", "key_env": "TOGETHER_AI_API_KEY"},
                "locus": "cloud", "privacy_class": "public",
                "price": {"input_per_mtok": a.price_in, "output_per_mtok": a.price_out,
                          "source": "user_declared"},
                "context_window": 200000, "output_budget_tokens": 512,
                "request_params": None, "timeout_s": 120.0, "connect_timeout_s": 10.0}
        conn["_key"] = __import__("os").environ.get("TOGETHER_AI_API_KEY")
    else:
        want = a.annotator_connection or ANNOTATOR_LABEL
        conn = next((c for c in conns if c["label"] == want), None)
    if conn is None:
        raise SystemExit(f"annotator not available (problems: {problems})")

    rows, t0, spend = [], time.time(), 0.0
    for i, it in enumerate(items, 1):
        rule_label, rank, _, _g = C.classify({"text": it["text"]}, rules)
        llm_label, status, cost = annotate(conn, it["text"])
        spend += cost
        rows.append({**it, "rules": rule_label, "annotator": llm_label,
                     "annotator_status": status})
        if i % 20 == 0 or i == len(items):
            print(f"  {i}/{len(items)} annotated")

    def pairs(key):
        return [(r["truth"], r[key]) for r in rows
                if r[key] in CLASSES]

    out = {}
    for name, key in (("rules_vs_author", "rules"), ("annotator_vs_author", "annotator")):
        ps = pairs(key)
        out[name] = {"n": len(ps), "accuracy": accuracy(ps), "kappa": kappa(ps),
                     "confusion": confusion(ps)}
    common = [(r["rules"], r["annotator"]) for r in rows
              if r["rules"] in CLASSES and r["annotator"] in CLASSES]
    out["rules_vs_annotator"] = {"n": len(common), "agreement": accuracy(common),
                                 "kappa": kappa(common), "confusion": confusion(common)}
    out["annotator_failures"] = dict(Counter(r["annotator_status"] for r in rows
                                             if r["annotator_status"] != "ok"))
    out["by_set"] = {}
    for s in sorted({r["set"] for r in rows}):
        rs = [r for r in rows if r["set"] == s and r["annotator"] in CLASSES]
        out["by_set"][s] = {"n": len(rs), "rules_vs_author": accuracy(
            [(r["truth"], r["rules"]) for r in rs if r["rules"] in CLASSES]),
            "annotator_vs_author": accuracy([(r["truth"], r["annotator"]) for r in rs])}
    disagreements = [{"id": r["id"], "truth": r["truth"], "rules": r["rules"],
                      "annotator": r["annotator"], "text": r["text"][:110]}
                     for r in rows if r["rules"] != r["annotator"]]

    print(f"\n{len(rows)} items in {time.time() - t0:.0f}s, ${spend:.6f} at list prices")
    for k in ("rules_vs_author", "annotator_vs_author", "rules_vs_annotator"):
        v = out[k]
        print(f"  {k:22} n={v['n']:3} agreement={v.get('accuracy', v.get('agreement'))} "
              f"kappa={v['kappa']}")
    print(f"  annotator failures: {out['annotator_failures'] or 'none'}")
    by_set = out["by_set"]
    print(f"  by set: {by_set}")
    print(f"  disagreements: {len(disagreements)}")
    for d in disagreements[:8]:
        print(f"    {d['id']:4} truth={d['truth']:12} rules={d['rules']:12} "
              f"annot={str(d['annotator']):12} {d['text'][:60]}")

    d = HERE / "results" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{a.out}.json").write_text(json.dumps(
        {"experiment": "second annotator (LLM) on the privacy battery",
         "annotator": conn["model"], "n_items": len(rows),
         "note": "the annotator is a model, not a human; battery is synthetic",
         "spend_usd": round(spend, 6),
         **out, "disagreements": disagreements}, indent=1, sort_keys=True) + "\n")
    print(f"\nfrozen: {(d / (a.out + '.json')).relative_to(L5)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
