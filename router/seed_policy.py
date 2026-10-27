#!/usr/bin/env python3
"""Seed ds:rules and ds:policy — the policy-as-data foundation (spec §1.4, §1.5).

Every tunable in this layer lives in these records. Nothing here may become a literal
in router code; if you find yourself wanting to hardcode a threshold, add it to the
policy record instead (owner caveat (a), 2026-10-22: end users will choose their own
parameters later).

Values are the owner-confirmed defaults of spec §12: gate order fixed; both cost knobs
at 0 as an explicit choice; missing measurements allowed but flagged.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from store import Store, new_id, now  # noqa: E402

PRIVACY_LABELS = ["private", "confidential", "public"]
TASK_LABELS = ["compute_via_code", "tabular_summary", "prose_summary", "multi_doc_selective",
               "claim_check", "structured_extract", "open_chat"]

# Ordered; first match wins. Private markers outrank public ones BY RANK, so a request
# that matches both ("translate my medical report") is classified private.
PRIVACY_RULES = [
    (1, "keyword_any", ["password", "passcode", "api key", "secret key", "private key",
                        "ssh key", "access token", "cvv", "pin code", "credentials", "two-factor"], "private"),
    (2, "keyword_any", ["medical", "diagnosis", "diagnosed", "prescription", "medication",
                        "symptom", "doctor", "clinic", "hospital", "health record", "therapy",
                        "blood test", "biopsy", "patient"], "private"),
    (3, "keyword_any", ["bank account", "account number", "credit card", "debit card",
                        "social security", "ssn", "tax return", "salary", "payslip",
                        "mortgage", "loan balance", "iban"], "private"),
    (4, "keyword_any", ["home address", "phone number", "email address", "date of birth",
                        "passport", "driver's license", "driving licence", "biometric",
                        "national id"], "private"),
    (5, "keyword_any", ["divorce", "custody", "lawsuit", "attorney", "legal claim",
                        "immigration case", "adoption", "restraining order"], "private"),
    (6, "keyword_any", ["performance review", "hr complaint", "disciplinary", "termination letter",
                        "employee record", "whistleblower"], "confidential"),
    (7, "keyword_any", ["client", "customer list", "contract", "unreleased", "roadmap",
                        "internal memo", "board deck", "acquisition", "nda", "pre-revenue",
                        "investor update"], "confidential"),
    (8, "keyword_any", ["published", "public documentation", "wikipedia", "open source",
                        "python", "javascript", "sql query", "recipe", "weather",
                        "define", "definition of", "history of", "translate this sentence",
                        "capital of", "how do i", "example code"], "public"),
]

# Ruleset v2, measured against exp2. Two changes, both in the SAFE direction:
#  (a) a first-person + personnel rule, so a personal disciplinary / review matter is
#      private rather than company-confidential. This is what fixes p11, the single
#      under-classification (M5) that leaked a genuinely private prompt into the v1 log.
#  (b) a much wider set of POSITIVE public markers at the LOWEST priority, so ordinary
#      technical and educational text stops falling through to the fail-closed default
#      (exp2 measured 2 benign questions defaulting to private, 15.9% over-classification).
# Severity ordering is preserved: every public rule sits below every private and
# confidential rule, so a prompt carrying sensitive vocabulary can never be pulled up to
# public by camouflage. Over-classification is accepted deliberately where narrowing a
# keyword would risk under-classification (u12 "credit card" in a Luhn tutorial, u13, u14):
# the asymmetry is that over-classification costs money, under-classification costs privacy.
PRIVACY_RULES_V2 = [
    (1, "keyword_any", ["password", "passcode", "api key", "secret key", "private key",
                        "ssh key", "access token", "cvv", "pin code", "credentials",
                        "two-factor"], "private"),
    (2, "keyword_any", ["medical", "diagnosis", "diagnosed", "prescription", "medication",
                        "symptom", "doctor", "clinic", "hospital", "health record", "therapy",
                        "blood test", "biopsy", "patient"], "private"),
    (3, "keyword_any", ["bank account", "account number", "credit card", "debit card",
                        "social security", "ssn", "tax return", "salary", "payslip",
                        "mortgage", "loan balance", "iban"], "private"),
    (4, "keyword_any", ["home address", "phone number", "email address", "date of birth",
                        "passport", "driver's license", "driving licence", "biometric",
                        "national id"], "private"),
    (5, "keyword_any", ["divorce", "custody", "lawsuit", "attorney", "legal claim",
                        "immigration case", "adoption", "restraining order"], "private"),
    (6, "keyword_any", ["disciplinary", "grievance", "termination letter"], "private"),
    (7, "keyword_any", ["performance review", "hr complaint", "employee record",
                        "whistleblower"], "confidential"),
    (8, "keyword_any", ["client list", "client contract", "client meeting", "our client",
                        "customers", "contract", "unreleased", "internal memo", "board deck",
                        "acquisition", "nda", "pre-revenue", "investor update",
                        "hiring freeze"], "confidential"),
    (9, "keyword_any", ["published", "public documentation", "press release", "wikipedia",
                        "open source", "python", "pandas", "javascript", "java", "sql query",
                        "csv file", "api", "tcp", "udp", "http", "hash chain", "database schema",
                        "algorithm", "recipe", "weather", "define", "definition of",
                        "history of", "capital of", "translate this sentence", "how do",
                        "how does", "what is", "what are", "difference between", "explain",
                        "example code", "in plain english", "best practice", "tutorial"],
     "public"),
]

# Ruleset v3: fixes only the regression v2 introduced. Dropping the bare word "client"
# (which in technical prose usually means a network client, not a customer) also stopped
# matching "customer list" and "client onboarding", so two genuinely confidential battery
# items fell through to the fail-closed default and became private. exp2 run v4 rose to
# 0.8864 accuracy and M5 hit 0, but c05 and c11 went the wrong way. Phrases added; ordering
# unchanged. This is a correction, not a new policy.
PRIVACY_RULES_V3 = [
    (8, "keyword_any",
     ["client list", "client contract", "client meeting", "client onboarding", "client data",
      "client account", "our client", "the client", "customer list", "customers", "contract",
      "unreleased", "internal memo", "board deck", "acquisition", "nda", "pre-revenue",
      "investor update", "hiring freeze"], "confidential")
    if r[0] == 8 else r
    for r in PRIVACY_RULES_V2
]

# Task ruleset v2 (owner principle, 2026-10-23): where code can compute the answer, the
# model writes the code and the code runs — the model never does arithmetic. So
# `compute_via_code` sits at the HIGHEST priority: a request asking for a program must not be
# classified as a plain structured-output request, because the two are graded on different
# things (does the program run and print the right value, vs does the reply parse as JSON).
TASK_RULES_V2 = [
    (1, "keyword_any", ["write a python program", "write a program", "python program that",
                        "write code that", "standard library only", "prints exactly this json"],
     "compute_via_code"),
    (2, "keyword_any", ["valid json", "json object", "return only json", "schema",
                        "extract the following fields", "structured output"], "structured_extract"),
    (3, "keyword_any", ["verify this claim", "does the data support", "check this binding",
                        "is this claim", "audit the claim", "recompute"], "claim_check"),
    (4, "keyword_any", ["across all", "all documents", "all datasets", "compare all",
                        "every file", "multi-document"], "multi_doc_selective"),
    (5, "attachment_type", ["csv", "tsv", "xlsx"], "tabular_summary"),
    (6, "keyword_any", ["table", "csv", "spreadsheet", "total of", "sum of", "aggregate",
                        "group by", "rows", "invoices", "expenses"], "tabular_summary"),
    (7, "attachment_type", ["pdf", "md", "txt"], "prose_summary"),
    (8, "keyword_any", ["summarise", "summarize", "summary of", "report", "article",
                        "document", "deck", "slides", "quarterly"], "prose_summary"),
]

TASK_RULES = [
    (1, "keyword_any", ["valid json", "json object", "return only json", "schema",
                        "extract the following fields", "structured output"], "structured_extract"),
    (2, "keyword_any", ["verify this claim", "does the data support", "check this binding",
                        "is this claim", "audit the claim", "recompute"], "claim_check"),
    (3, "keyword_any", ["across all", "all documents", "all datasets", "compare all",
                        "every file", "multi-document"], "multi_doc_selective"),
    (4, "attachment_type", ["csv", "tsv", "xlsx"], "tabular_summary"),
    (5, "keyword_any", ["table", "csv", "spreadsheet", "total of", "sum of", "aggregate",
                        "group by", "rows", "invoices", "expenses"], "tabular_summary"),
    (6, "attachment_type", ["pdf", "md", "txt"], "prose_summary"),
    (7, "keyword_any", ["summarise", "summarize", "summary of", "report", "article",
                        "document", "deck", "slides", "quarterly"], "prose_summary"),
]


def match_obj(t, v):
    """Payload key depends on match.type (see datasets/rules/schema.json invariants)."""
    if t in ("keyword_any", "keyword_all", "attachment_type"):
        return {"type": t, "values": list(v)}
    return {"type": t, "value": v}


def ruleset(kind, label, rules, default_label, label_set):
    return {
        "id": new_id("rul"), "created_at": now(), "spec_version": "1.1",
        "status": "active", "superseded_by": None, "source_ref": [],
        "rule_kind": kind, "version_label": label, "default_label": default_label,
        "rules": [{"rank": r, "match": match_obj(t, v), "label": lab} for r, t, v, lab in rules],
        "label_set": label_set,
        "validated_on": {"labelled_set": "", "n": 0, "accuracy": None},
    }


def main():
    store = Store(ROOT)

    have = {(r["rule_kind"], r["version_label"]) for r in store.records("rules", status="active")}
    ids = {}
    for kind, label, rules, default in (
            ("privacy", "v1", PRIVACY_RULES, "private"),
            ("privacy", "v2", PRIVACY_RULES_V2, "private"),
            ("privacy", "v3", PRIVACY_RULES_V3, "private"),
            ("task", "v1", TASK_RULES, "open_chat"),
            ("task", "v2", TASK_RULES_V2, "open_chat")):
        key = f"{kind}:{label}"
        if (kind, label) in have:
            ids[key] = next(r["id"] for r in store.records("rules", status="active")
                            if r["rule_kind"] == kind and r["version_label"] == label)
            print(f"skip (already active): {kind} rules {label} -> {ids[key]}")
            continue
        rec = ruleset(kind, label, rules, default,
                      PRIVACY_LABELS if kind == "privacy" else TASK_LABELS)
        store.append("rules", rec)
        ids[key] = rec["id"]
        print(f"registered {rec['id']}  {kind} rules {label}: {len(rules)} rules, default={default}")

    ids["privacy"] = ids["privacy:v1"]
    ids["task"] = ids["task:v1"]

    pol_have = [r for r in store.records("policy", status="active") if r["version_label"] == "v1"]
    if pol_have:
        print(f"skip (already active): policy v1 -> {pol_have[0]['id']}")
    else:
        pol = {
            "id": new_id("pol"), "created_at": now(), "spec_version": "1.1",
            "status": "active", "superseded_by": None,
            "source_ref": [ids["privacy"], ids["task"]],
            "version_label": "v1",
            "privacy": {
                "class_order": ["public", "confidential", "private"],
                "allowed_loci": {"private": ["local"], "confidential": ["local"],
                                 "public": ["local", "cloud"]},
                "unknown_request_class": "private",
            },
            "gates": {
                "order": ["privacy", "verifiability", "context_fit"],
                "verifiability": {"missing_measurement": "allow_flagged", "min_samples": 3},
                "context_fit": {"context_margin": 0.90, "check_local_resources": True},
            },
            "measurement": {
                "min_termination_rate": 0.99,
                "min_schema_compliance": 0.90,
                "min_accuracy": {"tabular_summary": 0.60, "prose_summary": 0.60,
                                 "multi_doc_selective": 0.60, "claim_check": 0.70,
                                 "structured_extract": 0.80, "open_chat": 0.0},
            },
            "objective": {
                "type": "min_declared_cost",
                "expected_output_tokens": {"tabular_summary": 600, "prose_summary": 600,
                                           "multi_doc_selective": 800, "claim_check": 200,
                                           "structured_extract": 400, "open_chat": 400},
                "tie_break": "id_asc",
                "escalation": {"triggers": ["structural_check_failed", "finish_reason_length",
                                            "confidence_below_min"],
                               "confidence_min": 0.70, "max_cascade_depth": 2},
            },
            "classifier": {"privacy_rules_ref": ids["privacy"], "task_rules_ref": ids["task"]},
            "budget": {"per_request_usd": None, "on_exceed": "alert_only"},
            "logging": {"store_prompt_content": "never_for_private",
                        "store_response_content": "never_for_private",
                        "store_gate_trace": "always",
                        "prompt_hash": {"alg": "hmac-sha256", "key_env": "LOG_HMAC_KEY"}},
        }
        store.append("policy", pol)
        print(f"registered {pol['id']}  policy v1 (refs {ids['privacy']}, {ids['task']})")

    # ---- v2: close the log-leak path that experiment 2 measured ----
    # v1 keyed the no-text rule to the CLASSIFIER's verdict ("never_for_private"). Experiment
    # 2 showed why that is not enough: prompt p11 (a personal disciplinary letter, labelled
    # private) classified as confidential, so its text was written to the decision log and
    # the proof-of-absence scan found it. v2 stores no prompt or response text for ANY
    # class, which removes the dependence on classifier correctness entirely. Identity is
    # still preserved by the HMAC, and gate 3 stays replayable from the logged est_tokens_in
    # (spec §1.6). v1 is left active and untouched so its decisions remain auditable.
    if not [r for r in store.records("policy", status="active") if r["version_label"] == "v2"]:
        import copy
        v1 = next(r for r in store.records("policy", status="active")
                  if r["version_label"] == "v1")
        v2 = copy.deepcopy(v1)
        v2["id"] = new_id("pol")
        v2["version_label"] = "v2"
        v2["created_at"] = now()
        v2["source_ref"] = [v1["id"]]
        v2["logging"]["store_prompt_content"] = "never"
        v2["logging"]["store_response_content"] = "never"
        store.append("policy", v2)
        print(f"registered {v2['id']}  policy v2 (no prompt/response text for any class; "
              f"supersedes nothing, v1 stays auditable)")
    else:
        print("skip (already active): policy v2")

    # ---- v3: an EXPERIMENTAL ARM, never the default ----
    # The owner's caveat (a) is that end users will configure the privacy-class -> locus map
    # themselves. This arm exercises exactly that: it is v2 with `confidential` allowed to
    # reach the cloud. Its purpose is to measure the safety consequence of a plausible user
    # choice, given a classifier that under-classifies at least one private prompt as
    # confidential (exp2, p11). is_default=false so route() never picks it implicitly.
    if not [r for r in store.records("policy", status="active")
            if r["version_label"] == "v3-conf-cloud"]:
        import copy
        v2 = next(r for r in store.records("policy", status="active")
                  if r["version_label"] == "v2")
        v3 = copy.deepcopy(v2)
        v3["id"] = new_id("pol")
        v3["version_label"] = "v3-conf-cloud"
        v3["created_at"] = now()
        v3["source_ref"] = [v2["id"]]
        v3["is_default"] = False
        v3["privacy"]["allowed_loci"]["confidential"] = ["local", "cloud"]
        store.append("policy", v3)
        print(f"registered {v3['id']}  policy v3-conf-cloud (EXPERIMENTAL ARM: confidential may "
              f"reach the cloud; is_default=false)")
    else:
        print("skip (already active): policy v3-conf-cloud")

    # ---- v4: privacy ruleset v2 + no-text logging, the configuration experiments 1/3 run on ----
    # v2 fixed WHERE text is stored; v4 fixes WHY p11 was exposed at all — the classifier
    # under-classified a personal employment matter. is_default=true makes this the implicit
    # policy, and v3 stays an explicitly-selected experimental arm.
    if not [r for r in store.records("policy", status="active") if r["version_label"] == "v4"]:
        import copy
        v2p = next(r for r in store.records("policy", status="active")
                   if r["version_label"] == "v2")
        v4 = copy.deepcopy(v2p)
        v4["id"] = new_id("pol")
        v4["version_label"] = "v4"
        v4["created_at"] = now()
        v4["is_default"] = True
        v4["classifier"]["privacy_rules_ref"] = ids["privacy:v2"]
        v4["source_ref"] = [v2p["id"], ids["privacy:v2"]]
        store.append("policy", v4)
        print(f"registered {v4['id']}  policy v4 (privacy ruleset v2, no-text logging, "
              f"is_default=true)")
    else:
        print("skip (already active): policy v4")

    # ---- v5: privacy ruleset v3 (regression fix) + no-text logging ----
    if not [r for r in store.records("policy", status="active") if r["version_label"] == "v5"]:
        import copy
        v4p = next(r for r in store.records("policy", status="active")
                   if r["version_label"] == "v4")
        v5 = copy.deepcopy(v4p)
        v5["id"] = new_id("pol")
        v5["version_label"] = "v5"
        v5["created_at"] = now()
        v5["is_default"] = True
        v5["classifier"]["privacy_rules_ref"] = ids["privacy:v3"]
        v5["source_ref"] = [v4p["id"], ids["privacy:v3"]]
        store.append("policy", v5)
        print(f"registered {v5['id']}  policy v5 (privacy ruleset v3, no-text logging, "
              f"is_default=true)")
    else:
        print("skip (already active): policy v5")

    # ---- v6: the owner's compute-via-code principle, expressed as policy DATA ----
    # "Whenever code can do the job, code should do the job" (owner, 2026-10-23). Encoded as a
    # preferred route plus a declared fallback. fallback="refuse" means: if no registered
    # variant is eligible to produce working code for a compute item, the router refuses rather
    # than letting a model do arithmetic in its head — the in-head answer is the one this
    # project has measured as unreliable (upstream 0/12; exp1 pilot: every arm wrong). An owner
    # who wants the opposite behaviour changes one field; no code edits.
    if not [r for r in store.records("policy", status="active") if r["version_label"] == "v6"]:
        import copy
        v5p = next(r for r in store.records("policy", status="active")
                   if r["version_label"] == "v5")
        v6 = copy.deepcopy(v5p)
        v6["id"] = new_id("pol")
        v6["version_label"] = "v6"
        v6["created_at"] = now()
        v6["is_default"] = True
        v6["source_ref"] = [v5p["id"], ids["task:v2"]]
        v6["classifier"]["task_rules_ref"] = ids["task:v2"]
        v6["measurement"]["min_accuracy"]["compute_via_code"] = 0.80
        v6["objective"]["compute_via_code"] = {"preferred": True,
                                               "applies_to": ["compute_via_code"],
                                               "fallback": "refuse"}
        store.append("policy", v6)
        print(f"registered {v6['id']}  policy v6 (compute-via-code preferred, fallback=refuse, "
              f"task ruleset v2, is_default=true)")
    else:
        print("skip (already active): policy v6")

    for ds in ("rules", "policy"):
        ok, detail = store.verify_chain(ds)
        print(f"[{'PASS' if ok else 'FAIL'}] chain {ds}: {detail}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
