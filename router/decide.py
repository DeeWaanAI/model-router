#!/usr/bin/env python3
"""route(request) -> one decision + one bound record (spec §2, §4, §5; deltas RC-1..RC-4).

What changed under RC and why the shape of this file changed with it:

* the candidate set is the **user's connect list** (`connections.load`), not a catalog;
* gate order now includes `availability`, because a dead endpoint is not a candidate
  (RC-4.3), and the breaker state is pinned in the record so replay stays exact;
* cost is the **declared** cost and may be `None`; `None` ranks last and is never invented;
* a live call can fail in three ways — typed error, structural check, or truncation — and
  each moves to the next feasible connection while the whole chain is recorded in ONE
  decision (RC-4.5/4.10);
* if every feasible connection fails, the outcome is a typed `refused` with
  `all_connections_failed`, never an exception and never an empty answer.

Everything the gates read still comes from frozen data; this module contains no tunable
literal. `dry_run=True` writes `outcome="planned"` and calls nothing.
"""
import hashlib, hmac, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import classify as C  # noqa: E402
import connections as X  # noqa: E402
import cost as K  # noqa: E402
import gates as G  # noqa: E402
import tokens as T  # noqa: E402
import transport as TX  # noqa: E402
import verifier as V  # noqa: E402
from health import Health  # noqa: E402
from store import Store, code_fingerprint, new_id, now  # noqa: E402

SECRETS = ROOT / ".secrets" / "log_hmac_key"
_DEFAULT_HEALTH = Health()


class RouteError(Exception):
    pass


def hmac_key():
    """Local key for the prompt HMAC (spec §1.6, §12.4). The key never enters a dataset, a
    log, or the repo: $LOG_HMAC_KEY, else the gitignored .secrets/ file."""
    env = os.environ.get("LOG_HMAC_KEY")
    if env:
        return env.encode()
    if SECRETS.exists():
        return SECRETS.read_text().strip().encode()
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32).hex()
    SECRETS.write_text(key + "\n")
    os.chmod(SECRETS, 0o600)
    return key.encode()


def prompt_hmac(text):
    return hmac.new(hmac_key(), (text or "").encode("utf-8"), hashlib.sha256).hexdigest()


def may_store_text(request_class, policy, field):
    mode = policy["logging"].get(field, "never")
    if mode == "never":
        return False
    if mode == "never_for_private":
        return request_class != "private"
    raise RouteError(f"unknown logging mode {mode!r} for {field}")


def structural_check(res, task_class):
    """Machine-checkable trigger: did the answer satisfy the output discipline its class
    requires? None when the class imposes none."""
    if task_class != "structured_extract":
        return None
    try:
        json.loads((res.get("text") or "").strip())
        return True
    except ValueError:
        return False


class Router:
    def __init__(self, store=None, transport=None, health=None, free_ram_gb=None,
                 policy_label=None):
        self.store = store or Store(ROOT)
        self.transport = transport or TX
        self.health = health or _DEFAULT_HEALTH
        self.free_ram_gb = free_ram_gb
        self.policy_label = policy_label

    # ---------- frozen inputs ----------
    def policy(self):
        act = self.store.records("policy", status="active")
        if not act:
            raise RouteError("no active ds:policy record")
        if self.policy_label is not None:
            sel = [p for p in act if p.get("version_label") == self.policy_label]
            if not sel:
                raise RouteError(f"no active ds:policy record labelled {self.policy_label!r}")
        else:
            sel = ([p for p in act if p.get("is_default") is True]
                   or [p for p in act if p.get("is_default") is not False]
                   or act)
        sel.sort(key=lambda p: p.get("created_at") or "")
        return sel[-1]

    def _ruleset(self, rid):
        r = self.store.get("rules", rid)
        if r is None or r.get("status") != "active":
            raise RouteError(f"ruleset {rid} is missing or not active")
        return r

    def _escalation_trigger(self, res, pol):
        esc = pol["objective"]["escalation"]
        for trig in esc["triggers"]:              # ranked order is policy data
            if trig == "structural_check_failed" and res.get("structural_check") is False:
                return trig
            if trig == "finish_reason_length" and res.get("finish_reason") == "length":
                return trig
            if (trig == "confidence_below_min" and res.get("confidence") is not None
                    and res["confidence"] < esc["confidence_min"]):
                return trig
            # Machine-checkable call failure outranks any self-reported signal (RC-3.3).
            if trig == "call_failed" and res.get("error_class"):
                return trig
        return None

    # ---------- the decision procedure ----------
    def route(self, request, dry_run=False, run=None):
        pol = self.policy()
        priv_rules = self._ruleset(pol["classifier"]["privacy_rules_ref"])
        task_rules = self._ruleset(pol["classifier"]["task_rules_ref"])
        conns, problems = X.load(self.store, env_file=X.ENV_FILE)
        caps = []
        for c in self.store.records("capabilities", status="active"):
            c = dict(c)
            c["_key"] = c.get("model_key") or X.capability_key(self.store, c.get("model_ref"))
            caps.append(c)
        text = request.get("text") or ""

        cmode = (pol.get("classifier") or {}).get("mode", "rules")
        if cmode == "trust_user":
            # Owner decision: the user connects the providers and owns the consequence, so the
            # router does not classify sensitivity at all — it routes on quality and cost. The
            # recorded class is "public" because nothing restricts it, and the record says so via
            # classifier_mode, so no auditor mistakes it for a computed verdict.
            pcls, prank, pguard = "public", None, None
        else:
            pcls, prank, _, pguard = C.classify(request, priv_rules)
        tcls, trank, _, _tguard = C.classify(request, task_rules)

        # Second opinion on a PUBLIC verdict (spec §19). Off unless the policy names a
        # connection. Runs before the gates, because the gates filter on the class.
        verification = None
        vlabel = (pol.get("classifier") or {}).get("verify_public_with")
        if vlabel and pcls == "public":
            vconn = next((c for c in conns if c["label"] == vlabel), None)
            if vconn is not None:
                verdict = V.check(self.transport, vconn, text)
                newcls, changed = V.apply(pcls, verdict)
                verification = {"model": vlabel, "verdict": verdict["label"],
                                "error": verdict["error"], "changed": changed,
                                "cost_usd": verdict.get("cost_usd")}
                pcls = newcls

        est_in, est_method, by_tok = {}, {}, {}
        for v in conns:
            ref = v.get("tokenizer_ref")
            if ref not in by_tok:
                by_tok[ref] = T.count_or_bound(text, ref)
            est_in[v["id"]], est_method[v["id"]] = by_tok[ref]
        exp_out = pol["objective"]["expected_output_tokens"].get(tcls, 0)

        health_snapshot = self.health.snapshot()
        runners = {
            "privacy": lambda cur: G.gate_privacy(cur, pcls, pol),
            "availability": lambda cur: G.gate_availability(cur, health_snapshot["unhealthy"]),
            "verifiability": lambda cur: G.gate_verifiability(cur, tcls, caps, pol),
            "context_fit": lambda cur: G.gate_context_fit(cur, est_in, exp_out, pol,
                                                          self.free_ram_gb),
        }
        trace, cur, emptied = [], conns, None
        for gname in pol["gates"]["order"]:
            fn = runners.get(gname)
            if fn is None:
                raise RouteError(f"policy names an unknown gate {gname!r}")
            s, x, n = fn(cur)
            trace.append({"gate": gname, "input_count": len(cur),
                          "survivors": [v["id"] for v in s], "excluded": x, "notes": n})
            cur = s
            if not cur:
                emptied = gname
                break

        by_id = {v["id"]: v for v in conns}
        base = dict(request=request, pol=pol, priv_rules=priv_rules, task_rules=task_rules,
                    pcls=pcls, tcls=tcls, prank=prank, trank=trank,
                    snapshot=self.store.registry_snapshot_hash(), trace=trace, run=run,
                    est_in=est_in, est_method=est_method, health_snapshot=health_snapshot,
                    problems=problems, pguard=pguard, verification=verification,
                    cmode=cmode)

        if emptied:
            return self._commit(outcome="refused",
                                refusal_reason=G.refusal_reason_for(emptied, trace[-1]["excluded"]),
                                feasible=[], chosen=None, attempts=[], chosen_model=None,
                                answer=None, **base)

        measured_keys = {c["_key"] for c in caps if c.get("prompt_class") == tcls}
        feasible = [{"model_ref": v["id"], "label": v.get("label"),
                     "est_tokens_in": est_in[v["id"]], "est_tokens_out": exp_out,
                     "cost_rankable": bool(v.get("cost_rankable")),
                     "measured_known": v.get("capability_key") in measured_keys,
                     "expected_cost_usd": K.expected_cost_usd(v, est_in[v["id"]], exp_out)}
                    for v in cur]
        ordered = K.rank(feasible, pol["objective"]["tie_break"])

        cap = (pol.get("budget") or {}).get("per_request_usd")
        head = ordered[0]["expected_cost_usd"]
        alert = bool(cap is not None and head is not None and head > cap)
        if alert and (pol.get("budget") or {}).get("on_exceed") == "refuse":
            return self._commit(outcome="refused", refusal_reason="budget_exceeded",
                                feasible=ordered, chosen=None, attempts=[], chosen_model=None,
                                answer=None, budget_alert=True, **base)

        if dry_run:
            winner = by_id[ordered[0]["model_ref"]]
            return self._commit(outcome="planned", refusal_reason=None, feasible=ordered,
                                chosen={"model_ref": winner["id"], "attempt": 1}, attempts=[],
                                chosen_model=winner, answer=None, budget_alert=alert, **base)

        attempts, answer, winner, trigger = [], None, None, None
        depth = pol["objective"]["escalation"]["max_cascade_depth"]
        for i, cand in enumerate(ordered):
            if i >= depth:
                break
            v = by_id[cand["model_ref"]]
            res = self.transport.call(v, text, cand["est_tokens_out"])
            if res["ok"]:
                self.health.record_success(v["id"])
            else:
                self.health.record_failure(v["id"], res["error_class"])
            winner = v if res["ok"] else winner
            attempts.append({
                "model_ref": v["id"], "label": v.get("label"), "attempt": i + 1,
                "triggered_by": trigger,
                "est_cost_usd": cand["expected_cost_usd"],
                "actual_cost_usd": K.actual_cost_usd(v, res.get("tokens_in"), res.get("tokens_out")),
                "tokens_in": res.get("tokens_in"), "tokens_out": res.get("tokens_out"),
                "meter": res.get("meter"), "error_class": res.get("error_class"),
                "tries": res.get("tries", []),
                "finish_reason": res.get("finish_reason"),
                "structural_check": (structural_check(res, tcls) if res["ok"] else None),
                "confidence": res.get("confidence"),
                "wall_clock_s": round(res.get("latency_ms", 0) / 1000.0, 3),
            })
            if not res["ok"]:
                trigger = "call_failed"            # fall back to the next feasible (RC-4.5)
                continue
            answer = res.get("text")
            trigger = self._escalation_trigger(attempts[-1], pol)
            if trigger is None:
                break

        if winner is None or not any(a.get("error_class") is None for a in attempts):
            return self._commit(outcome="refused", refusal_reason="all_connections_failed",
                                feasible=ordered, chosen=None, attempts=attempts,
                                chosen_model=None, answer=None, budget_alert=alert, **base)

        return self._commit(outcome="escalated" if len(attempts) > 1 else "answered",
                            refusal_reason=None, feasible=ordered,
                            chosen={"model_ref": winner["id"], "attempt": len(attempts)},
                            attempts=attempts, chosen_model=winner, answer=answer,
                            budget_alert=alert, **base)

    # ---------- record + binding, written once ----------
    def _commit(self, request, pol, priv_rules, task_rules, pcls, tcls, prank, trank,
                snapshot, trace, run, est_in, est_method, health_snapshot, problems, pguard,
                verification, cmode,
                outcome, refusal_reason, feasible, chosen, attempts, chosen_model, answer,
                budget_alert=False):
        dec_id = new_id("dec")
        store_prompt = may_store_text(pcls, pol, "store_prompt_content")
        store_answer = answer if (answer is not None
                                  and may_store_text(pcls, pol, "store_response_content")) else None
        binding = self._binding(dec_id, pol, pcls, outcome, chosen_model, feasible, trace,
                                priv_rules, task_rules)
        # The user owns this decision (owner, 2026-10-25). The router records it loudly and
        # gets out of the way; it does not veto a locus the user's own policy permits.
        warn_for = (pol.get("privacy") or {}).get("warn_on_cloud_for") or []
        privacy_warning = None
        if chosen_model and chosen_model["locus"] == "cloud" and pcls in warn_for:
            privacy_warning = (f"{pcls}-classified content goes to a cloud endpoint "
                               f"({chosen_model.get('label')}); permitted by the user's "
                               f"policy — recorded here and shown to the user")
        rec = {
            "id": dec_id, "created_at": now(), "spec_version": "1.1",
            "status": "active", "superseded_by": None,
            "source_ref": [pol["id"], priv_rules["id"], task_rules["id"]],
            "request_ref": request.get("request_id") or new_id("req"),
            "policy_ref": pol["id"],
            "privacy_rules_ref": priv_rules["id"], "task_rules_ref": task_rules["id"],
            "registry_snapshot_hash": snapshot,
            "code_fingerprint": code_fingerprint(),
            "privacy_class": pcls, "task_class": tcls, "classifier_matched_rank": prank,
            "classifier_mode": cmode, "classifier_guard": pguard,
            "classifier_verification": verification,
            "gate_trace": trace, "feasible_set": feasible,
            "est_tokens_in": est_in, "est_tokens_method": est_method,
            "health_snapshot": health_snapshot,
            "registry_provenance": {"connections": {v: "user_declared"
                                                    for v in ("locus", "privacy_class", "price")},
                                    "unusable_connections": problems},
            "outcome": outcome, "refusal_reason": refusal_reason, "chosen": chosen,
            "privacy_warning": privacy_warning,
            "attempts": attempts,
            "prompt_hmac": prompt_hmac(request.get("text")),
            "prompt_text": request.get("text") if store_prompt else None,
            "response_text": store_answer,
            "budget_alert": bool(budget_alert), "binding_ref": binding["id"], "run": run,
        }
        self.store.append("decision_log", rec)
        self.store.append_binding("decision_log", binding)

        est = sum(a["est_cost_usd"] or 0.0 for a in attempts) if attempts else \
            (feasible[0]["expected_cost_usd"] or 0.0 if feasible else 0.0)
        actuals = [a["actual_cost_usd"] for a in attempts if a.get("actual_cost_usd") is not None]
        act = sum(actuals) if actuals else None
        return {"outcome": outcome, "decision_ref": dec_id, "binding_ref": binding["id"],
                "chosen": ({"model_ref": chosen_model["id"], "label": chosen_model.get("label"),
                            "connector": chosen_model.get("connector"),
                            "locus": chosen_model["locus"]} if chosen_model else None),
                "refusal_reason": refusal_reason, "privacy_warning": privacy_warning,
                "privacy_class": pcls, "task_class": tcls,
                "feasible_count": len(feasible),
                # Two numbers, never conflated (RC-3.5): provider meters and declared prices.
                "cost": {"list_price_estimate_usd": est, "actual_spend_usd": act},
                "attempts": [{"model_ref": a["model_ref"], "error_class": a["error_class"],
                              "triggered_by": a["triggered_by"]} for a in attempts],
                "answer": answer}

    def _binding(self, dec_id, pol, pcls, outcome, chosen_model, feasible, trace,
                 priv_rules, task_rules):
        """Per-decision claim binding. `value` is the minimum DECLARED cost, or null when no
        feasible connection declares a price — a null is honest, a fabricated number is not
        (RC-1.4). The winner's identity is proved by the §6.1 replay check."""
        if outcome == "refused":
            derivation = "count(filter(active_connections, privacy and availability and verifiability and context_fit))"
            value, statistic = 0, "count"
            assertion = {"statement": "no connected model satisfied every gate",
                         "predicate": "scope_enumeration", "expected": 0, "tolerance": 0,
                         "both_sides": {"connections_considered": trace[0]["input_count"],
                                        "survivors": 0}}
        else:
            costs = [f["expected_cost_usd"] for f in feasible if f["expected_cost_usd"] is not None]
            derivation = ("min(expected_cost_usd) filter(active_connections, "
                          "privacy and availability and verifiability and context_fit)")
            value, statistic = (min(costs) if costs else None), "min"
            allowed = pol["privacy"]["allowed_loci"].get(pcls, [])
            locus = chosen_model["locus"] if chosen_model else None
            assertion = {"statement": f"the locus chosen for this {pcls}-classified request "
                                      f"is one the policy permits",
                         "predicate": "label_match", "expected": allowed, "tolerance": None,
                         "both_sides": {"allowed_loci": allowed, "chosen_locus": locus,
                                        "cost_rankable": bool(costs)}}
        return {"id": new_id("b"), "kind": "claim",
                "target": {"dataset": "ds:decision_log", "record": dec_id},
                "sources": [{"dataset": "ds:policy", "record": pol["id"], "field": "gates"},
                            {"dataset": "ds:policy", "record": pol["id"], "field": "privacy"},
                            {"dataset": "ds:rules", "record": priv_rules["id"], "field": "rules"},
                            {"dataset": "ds:rules", "record": task_rules["id"], "field": "rules"}],
                "derivation": derivation, "value": value, "statistic": statistic,
                "device": "local-m1pro",
                "authoritative_artifact": "datasets/decision_log/data.jsonl",
                "assertion": assertion,
                "provenance": {"created_by": "llm:deewaanai-l5-router", "created_at": now()},
                "check": {"provenance": "PENDING", "semantic": "PENDING"}}


def aggregate_privacy_binding(store, run, policy_id):
    """The paper's headline claim: a count over the frozen log with both sides recorded."""
    recs = [r for r in store.records("decision_log") if run is None or r.get("run") == run]
    priv = [r for r in recs if r["privacy_class"] == "private"]
    violations = []
    for r in priv:
        ref = (r.get("chosen") or {}).get("model_ref")
        m = store.get("models", ref) if ref else None
        if m and m.get("locus") == "cloud":
            violations.append(r)
    b = {"id": new_id("b"), "kind": "claim",
         "target": {"dataset": "ds:decision_log", "record": None,
                    "scope": f"run={run}" if run else "all"},
         "sources": [{"dataset": "ds:decision_log",
                      "filter": (f"run={run}" if run else "all"), "field": "privacy_class"},
                     {"dataset": "ds:policy", "record": policy_id, "field": "privacy"}],
         "derivation": "count(filter(decision_log, privacy_class='private' and chosen.locus='cloud'))",
         "value": len(violations), "statistic": "count", "device": "local-m1pro",
         "authoritative_artifact": "datasets/decision_log/data.jsonl",
         "assertion": {"statement": "no private-classified request was routed to a cloud locus",
                       "predicate": "scope_enumeration", "expected": 0, "tolerance": 0,
                       "both_sides": {"private_requests": len(priv),
                                      "private_routed_to_cloud": len(violations)}},
         "provenance": {"created_by": "llm:deewaanai-l5-router", "created_at": now()},
         "check": {"provenance": "PENDING", "semantic": "PENDING"}}
    store.append_binding("decision_log", b)
    return b, {"private_requests": len(priv), "violations": len(violations),
               "violating_decisions": [r["id"] for r in violations]}
