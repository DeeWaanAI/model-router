#!/usr/bin/env python3
"""Deterministic rule matching for ds:rules (spec §1.5, §2.1, §2.2).

Ordered evaluation, first match wins, ties by array position. No learning, no
randomness: a decision is replayable from the ruleset alone.

Trust note: regex patterns come from local policy data that the owner will eventually
edit. They are compiled once and a pattern that fails to compile is skipped and reported
— never silently ignored. Do not extend this to evaluate arbitrary expressions from
data; see ELIGIBILITY_DERIVATIONS in gates.py for why named recipes are used instead.
"""
import re

_COMPILED = {}


def _keyword_pattern(kw):
    """Keywords match on WORD BOUNDARIES, never as bare substrings.

    Measured defect, exp2 runs before 2026-10-22: the confidential keyword `nda` matched
    inside "pa-nda-s", so "Show me how to read a CSV file in pandas" was classified
    confidential instead of falling through to the fail-closed default. Substring matching
    turns every short keyword into a false-positive generator, and the error stays invisible
    until a labelled battery is run against it.

    Scope of this defect, stated precisely so it is not over-claimed: it caused a
    MISCLASSIFICATION, not the log leak. The leak (prompt p11) came from a correct
    whole-word match on "disciplinary" at the wrong severity, combined with a logging rule
    that keyed text storage to the classifier's verdict. Two defects, two fixes: this one
    is the matcher; the other is policy v2 (store no text) plus a ruleset severity change.
    """
    key = ("kw", kw)
    if key not in _COMPILED:
        _COMPILED[key] = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    return _COMPILED[key]


def _regex(pattern):
    if ("re", pattern) not in _COMPILED:
        _COMPILED[("re", pattern)] = re.compile(pattern, re.IGNORECASE)
    return _COMPILED[("re", pattern)]


def _sorted_rules(ruleset):
    return sorted(enumerate(ruleset["rules"]), key=lambda p: (p[1].get("rank", 0), p[0]))


def _matches(match, text, attachments, dataset_refs):
    t = match.get("type")
    if t in ("keyword_any", "keyword_all"):
        vals = match.get("values", [])
        hits = [v for v in vals if _keyword_pattern(v).search(text)]
        return bool(hits) if t == "keyword_any" else len(hits) == len(vals) and bool(vals)
    if t == "regex":
        try:
            return bool(_regex(match["value"]).search(text))
        except (re.error, KeyError):
            return False
    if t == "attachment_type":
        types = {a.get("type") for a in attachments}
        return bool(types & set(match.get("values", [])))
    if t == "dataset_ref":
        return match.get("value") in dataset_refs
    raise ValueError(f"unknown match type {t!r}")


def _public_guard(text, guard):
    """Demote a PUBLIC match when the text names a personal or business domain.

    Why this exists instead of more keywords. The held-out battery failed in the dangerous
    direction three times, and every failure had the same shape: a weak public marker
    ("explain", "summarise") won because the sensitive domain noun was not in the private
    vocabulary ("my transcript", "freight costs", "tenancy agreement"). Adding those three
    phrases fixes three items and nothing else. A guard fixes the *shape*: a public verdict
    is only allowed when the text names no domain that can carry personal or internal data.

    It can only move a classification toward the restrictive side, so it cannot create a new
    under-classification. The cost is over-classification of general questions that happen to
    mention a domain word, which is the direction this layer already chooses to fail in.
    """
    if not guard:
        return None
    for term in guard.get("personal", []):
        if _keyword_pattern(term).search(text):
            return {"demoted_from": "public", "to": "private", "term": term}
    for term in guard.get("business", []):
        if _keyword_pattern(term).search(text):
            return {"demoted_from": "public", "to": "confidential", "term": term}
    return None


def classify(request, ruleset):
    """Return (label, matched_rank, skipped_patterns, guard).

    matched_rank is None when the fail-closed / default label applied — which is itself a
    measured quantity (privacy-label coverage, spec §6.3). `guard` is None unless a public
    match was demoted, and is recorded in the decision so the reason is auditable."""
    text = request.get("text") or ""
    attachments = request.get("attachments") or []
    dataset_refs = set(request.get("dataset_refs") or [])
    skipped = []
    for _, rule in _sorted_rules(ruleset):
        m = rule.get("match") or {}
        if m.get("type") == "regex":
            try:
                _regex(m["value"])
            except re.error:
                skipped.append(m.get("value"))
                continue
        if _matches(m, text, attachments, dataset_refs):
            if rule["label"] == "public":
                g = _public_guard(text, ruleset.get("public_guard"))
                if g:
                    return g["to"], rule.get("rank"), skipped, g
            return rule["label"], rule.get("rank"), skipped, None
    return ruleset["default_label"], None, skipped, None


def clearance_ok(model_clearance, request_class, class_order):
    """model.clearance ⊒ request.class (spec §2.1). Ordering comes from policy data."""
    if model_clearance not in class_order or request_class not in class_order:
        return False
    return class_order.index(model_clearance) >= class_order.index(request_class)
