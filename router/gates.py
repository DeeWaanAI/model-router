#!/usr/bin/env python3
"""The three gates (spec §2.1–§2.3) as pure functions over frozen data.

No tunable is read from anywhere but the policy record. Each gate returns
(survivors, excluded, notes) so the caller can write a full gate_trace — the evidence
that makes a decision replayable by an auditor who does not trust this code (§6.1).

Eligibility is computed twice, deliberately:
  * `recompute_eligible(row)` uses the row's own thresholds_snapshot — that is the
    audit-stable verdict Layer 1 recomputes to prove the stored `eligible` is right.
  * `eligibility_under(row, thresholds, cls)` uses the CURRENT policy thresholds — that
    is what routing uses, so changing min_accuracy in ds:policy changes routing
    immediately without touching a single capability row (owner caveat (a)).
Neither path evaluates an expression taken from data. `eligibility_derivation` names a
recipe from the closed registry below; a policy file is user-editable data and
eval'ing strings from it would be arbitrary code execution in a public AGPL repo.
"""

from classify import clearance_ok


class GateError(Exception):
    pass


ELIGIBILITY_DERIVATIONS = {
    "termination_and_schema_and_accuracy_v1": lambda m, t, cls: (
        m.get("termination_rate", 0.0) >= t["min_termination_rate"]
        and m.get("schema_compliance", 0.0) >= t["min_schema_compliance"]
        and m.get("accuracy", 0.0) >= t["min_accuracy"].get(cls, 0.0)
    ),
}


def _recipe(row):
    fn = ELIGIBILITY_DERIVATIONS.get(row.get("eligibility_derivation"))
    if fn is None:
        raise GateError(f"capability {row.get('id')}: unknown eligibility_derivation "
                        f"{row.get('eligibility_derivation')!r}; known: "
                        f"{sorted(ELIGIBILITY_DERIVATIONS)}")
    return fn


def recompute_eligible(row):
    """The row's own verdict, from its own snapshot. Layer 1 recomputes this (§6.1.3)."""
    snap = row.get("thresholds_snapshot") or {}
    return bool(_recipe(row)(row["measured"], snap, row["prompt_class"]))


def eligibility_under(row, thresholds, prompt_class):
    """The verdict under a given policy's thresholds — what routing uses."""
    return bool(_recipe(row)(row["measured"], thresholds, prompt_class))


def _which_metric_failed(row, thresholds, prompt_class):
    m, out = row["measured"], []
    if m.get("termination_rate", 0.0) < thresholds["min_termination_rate"]:
        out.append(f"termination_rate={m.get('termination_rate')}<{thresholds['min_termination_rate']}")
    if m.get("schema_compliance", 0.0) < thresholds["min_schema_compliance"]:
        out.append(f"schema_compliance={m.get('schema_compliance')}<{thresholds['min_schema_compliance']}")
    floor = thresholds["min_accuracy"].get(prompt_class, 0.0)
    if m.get("accuracy", 0.0) < floor:
        out.append(f"accuracy={m.get('accuracy')}<{floor}")
    return ",".join(out) or "unknown"


# ---------------- gate 1: privacy (hard) ----------------

def gate_privacy(variants, request_class, policy):
    priv = policy["privacy"]
    allowed = priv["allowed_loci"].get(request_class)
    survivors, excluded, notes = [], [], []
    if allowed is None:
        # No mapping for this class: fail closed, exclude everything (§2.1).
        for v in variants:
            excluded.append({"model_ref": v["id"], "reason": f"no_locus_mapping_for_class:{request_class}"})
        notes.append(f"policy has no allowed_loci mapping for {request_class!r}; failed closed")
        return survivors, excluded, notes
    for v in variants:
        # `privacy_class` is the RC-1 field; pre-RC records called the same thing `clearance`.
        # The fallback exists so historical decisions still replay, not as a data model.
        declared = v.get("privacy_class") or v.get("clearance")
        if v["locus"] not in allowed:
            excluded.append({"model_ref": v["id"], "reason": f"locus_{v['locus']}_not_allowed_for_{request_class}"})
        elif not clearance_ok(declared, request_class, priv["class_order"]):
            # The reason string is part of the recorded evidence: a pre-RC record used the
            # `clearance` vocabulary and its logged trace is replayed verbatim, so the wording
            # follows the record's vocabulary rather than being modernised under it.
            why = (f"declared_privacy_class_{declared}_below_{request_class}"
                   if "privacy_class" in v else f"clearance_{declared}_below_{request_class}")
            excluded.append({"model_ref": v["id"], "reason": why})
        else:
            survivors.append(v)
    return survivors, excluded, notes


# ---------------- availability (circuit breaker) ----------------

def gate_availability(variants, unavailable_ids):
    """RC-4.3: an endpoint the breaker has opened is not a candidate. The state is pinned in
    the decision record (`health_snapshot`), so replay reproduces this gate exactly."""
    unavailable_ids = set(unavailable_ids or ())
    survivors, excluded = [], []
    for v in variants:
        if v["id"] in unavailable_ids:
            excluded.append({"model_ref": v["id"], "reason": "unhealthy"})
        else:
            survivors.append(v)
    return survivors, excluded, []


# ---------------- gate 2: verifiability (eligibility) ----------------

def gate_verifiability(variants, task_class, capabilities, policy):
    conf = policy["gates"]["verifiability"]
    mode = conf["missing_measurement"]
    min_samples = conf["min_samples"]
    thresholds = policy["measurement"]
    survivors, excluded, notes = [], [], []

    for v in variants:
        # Capabilities attach to the MODEL behind the connection, not to the registry row
        # (RC-1: a connection record can be replaced while the model is the same). A pre-RC
        # record predates `capability_key`; its `upstream_id` is the same string.
        vkey = v.get("capability_key") or v.get("upstream_id") or v.get("model")
        # A pre-RC record was routed under a gate that also matched the output budget; its
        # logged traces must replay exactly, so the legacy rule is kept for those records
        # only. A connection (has `connector`) ignores the budget and records a note instead.
        legacy = "connector" not in v
        rows = [c for c in capabilities
                if c.get("_key") == vkey
                and (not legacy or c.get("output_budget_tokens") == v.get("output_budget_tokens"))
                and c.get("prompt_class") == task_class
                and c.get("status") == "active"]
        rows.sort(key=lambda c: c.get("created_at") or "")
        row = rows[-1] if rows else None
        if row is not None and row.get("output_budget_tokens") != v.get("output_budget_tokens"):
            notes.append({"model_ref": v["id"], "flag": "measured_at_other_budget",
                          "row_budget": row.get("output_budget_tokens"),
                          "connection_budget": v.get("output_budget_tokens")})

        if row is None:
            if mode == "refuse":
                excluded.append({"model_ref": v["id"], "reason": f"no_measurement_strict:{task_class}"})
            else:
                survivors.append(v)
                if mode == "allow_flagged":
                    notes.append({"model_ref": v["id"], "flag": "unmeasured",
                                  "prompt_class": task_class})
            continue

        if eligibility_under(row, thresholds, task_class):
            survivors.append(v)
            continue

        # A disqualification needs enough samples to stand (spec §1.3). Too small a
        # sample cannot exclude a model — it is treated as no measurement at all.
        if (row.get("n") or 0) < min_samples:
            if mode == "refuse":
                excluded.append({"model_ref": v["id"],
                                 "reason": f"insufficient_n_and_strict:n={row.get('n')}<{min_samples}"})
            else:
                survivors.append(v)
                notes.append({"model_ref": v["id"], "flag": "insufficient_n",
                              "n": row.get("n"), "min_samples": min_samples})
            continue

        excluded.append({"model_ref": v["id"],
                         "reason": "ineligible:" + _which_metric_failed(row, thresholds, task_class)})
    return survivors, excluded, notes


# ---------------- gate 3: context_fit ----------------

def gate_context_fit(variants, est_tokens_in, expected_out, policy, free_ram_gb=None):
    conf = policy["gates"]["context_fit"]
    margin = conf["context_margin"]
    check_ram = conf.get("check_local_resources", False)
    survivors, excluded, notes = [], [], []
    for v in variants:
        need = est_tokens_in[v["id"]] + expected_out
        room = margin * v["context_window"]
        if need > room:
            excluded.append({"model_ref": v["id"],
                             "reason": f"context_exceeds:need={need}>room={room:.0f}"
                                       f"(window={v['context_window']},margin={margin})"})
            continue
        if check_ram and v["locus"] == "local":
            lr = v.get("local_resources") or {}
            need_ram = lr.get("min_free_ram_gb")
            if need_ram is None:
                # A user's connect list does not have to declare RAM. Excluding an undeclared
                # endpoint would make the shipped policy refuse every user connection, which
                # is the opposite of what RC-1 asks for. Unchecked is recorded, not punished.
                notes.append({"model_ref": v["id"], "flag": "ram_undeclared"})
                survivors.append(v)
                continue
            if free_ram_gb is None:
                notes.append({"model_ref": v["id"], "flag": "ram_unchecked"})
            elif need_ram > free_ram_gb:
                excluded.append({"model_ref": v["id"],
                                 "reason": f"local_resource_unavailable:needs={need_ram}GB>"
                                           f"free={free_ram_gb}GB"})
                continue
        survivors.append(v)
    return survivors, excluded, notes


GATES = {"privacy": gate_privacy, "availability": gate_availability,
         "verifiability": gate_verifiability, "context_fit": gate_context_fit}


def refusal_reason_for(gate_name, excluded):
    """Map the gate that emptied the set to a §2.6 reason code."""
    reasons = [e["reason"] for e in excluded]
    if gate_name == "privacy":
        if any(r.startswith("no_locus_mapping") for r in reasons):
            return "classifier_default_fail_closed"
        return "no_variant_for_privacy_class"
    if gate_name == "availability":
        # Every candidate is cooling down because its calls failed: that is the failure
        # outcome, and it is reported as one (RC-4.10), not disguised as a privacy refusal.
        return "all_connections_failed"
    if gate_name == "verifiability":
        return "no_eligible_variant_for_class"
    if any(r.startswith("local_resource_unavailable") or r == "local_resources_missing" for r in reasons):
        return "local_resource_unavailable"
    return "context_exceeds_all"
