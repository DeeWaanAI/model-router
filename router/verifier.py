#!/usr/bin/env python3
"""verifier.py — optional second opinion on a PUBLIC classification.

Why this exists. The rules classify 54% of a fresh out-of-domain battery correctly, and seven
private prompts were labelled public. Two local fixes were measured and both fail: adding phrases
moves the problem, and requiring two public markers trades the dangerous error for a much larger
volume of over-classification (accuracy 0.50, 33 of 72 over-classified). A capable model reading
the text reaches 0.93 on the same set with three residual cases that the independent annotator also
disagrees with us on.

So the mechanism is a second opinion, and it is **off by default** for one reason: a verifier has
to read the prompt to say whether it is private, so enabling a *cloud* verifier sends every
public-looking prompt to that provider — including the ones the rules got wrong. That is the
owner's call, made in policy, not a default this layer should choose.

The failure behaviour is deliberate: if the verifier cannot answer, the verdict falls to
`confidential`, not back to `public`. An unverifiable public claim is not a public claim.
"""
import json, re

CLASSES = ("public", "confidential", "private")
ORDER = {"public": 0, "confidential": 1, "private": 2}

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


def parse(text):
    raw = (text or "").strip()
    start = raw.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    except ValueError:
        return None
    label = str(obj.get("class", "")).lower()
    return label if label in CLASSES else None


def check(transport, conn, text):
    """Returns {label, error, cost_usd, tokens_in, tokens_out}. Never raises."""
    res = transport.call(conn, RUBRIC + text, 512)
    out = {"label": None, "error": None, "cost_usd": None, "model": conn.get("label")}
    if not res["ok"]:
        out["error"] = res["error_class"]
        return out
    out["label"] = parse(res.get("text"))
    if out["label"] is None:
        out["error"] = "unparsed"
    if res.get("tokens_in") is not None:
        p = conn.get("price") or {}
        out["cost_usd"] = (res["tokens_in"] * p.get("input_per_mtok", 0.0)
                           + res["tokens_out"] * p.get("output_per_mtok", 0.0)) / 1e6
        out["tokens_in"], out["tokens_out"] = res["tokens_in"], res["tokens_out"]
    return out


def apply(rule_label, verdict):
    """The verified class, and whether it changed.

    A stricter verdict always wins. A failed verification demotes to confidential — never back
    to public — because an unverifiable public claim is not a public claim.
    """
    if rule_label != "public" or verdict is None:
        return rule_label, False
    if verdict["label"] is None:
        return "confidential", True
    if ORDER[verdict["label"]] > ORDER[rule_label]:
        return verdict["label"], True
    return rule_label, False
