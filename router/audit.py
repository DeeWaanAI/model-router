#!/usr/bin/env python3
"""The Layer-1 replay audit (spec §6.1), the log-privacy proof-of-absence scan (§6.2),
and the coverage numbers (§6.3). Exit 0 or it does not; there is no percentage to
negotiate with.

The audit trusts NOTHING derived: it rebuilds the registry as it stood at each decision's
timestamp from the chained history, re-runs the three gates, and requires the recomputed
survivor sets to equal the logged gate_trace exactly.

One honest boundary, stated rather than hidden: gate 3 needs a token count, and for a
private request the prompt text is deliberately not stored (§12.4). So for private
decisions the audit verifies gate 3's ARITHMETIC against the recorded est_tokens_in but
cannot re-derive that number from text. For non-private decisions, where the text is
stored, it recomputes est_tokens_in from the variant's own tokenizer and checks it too.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import connections as X  # noqa: E402
import gates as G  # noqa: E402
import tokens as T  # noqa: E402
from cost import actual_cost_usd, expected_cost_usd, rank  # noqa: E402
from store import DATASETS, Store, canon  # noqa: E402

PRICE_CEILING = 1000.0
LOG_FILES = ("data.jsonl", "history.jsonl", "bindings.jsonl")


class Report:
    def __init__(self):
        self.checks, self.fails = [], []

    def check(self, label, cond, detail=""):
        self.checks.append({"label": label, "pass": bool(cond), "detail": str(detail)[:300]})
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            self.fails.append(label)
        return bool(cond)


def registry_at(store, when):
    """The active model records as they stood at `when`, rebuilt from chained history.
    Succession (§9.1) means today's registry is NOT the registry a past decision saw."""
    state = {}
    hp = store._dir("models") / "history.jsonl"
    for line in hp.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if (e.get("at") or "") > when:
            break
        rec = e.get("record") or {}
        if e.get("op") == "append":
            state[rec["id"]] = rec
        elif e.get("op") == "status":
            state[rec["id"]] = rec.get("after") or {}
    return [r for r in state.values() if r.get("status") == "active"]


def data_matches_history(store, ds):
    """Rebuild the dataset from its chained history and compare with data.jsonl.

    The chain proves the HISTORY was not edited; on its own it says nothing about the file
    the router actually reads. Without this reconciliation a record can be altered, inserted
    or deleted in data.jsonl and every hash still verifies — found while writing the
    fault-injection test, which is the point of having one.
    """
    hp = store._dir(ds) / "history.jsonl"
    state = {}
    for line in hp.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        op = e.get("op")
        if op == "append":
            state[e["record"]["id"]] = e["record"]
        elif op == "status":
            state[e["record"]["id"]] = e["record"]["after"]
    disk = {}
    for line in (store._dir(ds) / "data.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            disk[r["id"]] = r
    if set(state) != set(disk):
        return False, (f"ids differ: history-only={sorted(set(state) - set(disk))[:3]} "
                       f"disk-only={sorted(set(disk) - set(state))[:3]}")
    bad = sorted(i for i in state if canon(state[i]) != canon(disk[i]))
    return (not bad), f"{len(disk)} records" + (f"; mismatched={bad[:3]}" if bad else "")


def capabilities_at(store, when):
    state = {}
    hp = store._dir("capabilities") / "history.jsonl"
    for line in hp.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if (e.get("at") or "") > when:
            break
        rec = e.get("record") or {}
        if e.get("op") == "append":
            state[rec["id"]] = rec
        elif e.get("op") == "status":
            state[rec["id"]] = rec.get("after") or {}
    return [r for r in state.values() if r.get("status") == "active"]


def replay(dec, pol, store):
    """Re-run the three gates for one decision against the registry as it stood then."""
    when = dec["created_at"]
    variants = registry_at(store, when)
    caps = capabilities_at(store, when)
    by_id = {v["id"]: v for v in variants}
    notes = []

    # est_tokens_in: recomputed from the text when we are allowed to hold it; otherwise the
    # logged per-variant estimate is used, and the boundary is recorded (module docstring).
    text = dec.get("prompt_text")
    logged_est = dict(dec.get("est_tokens_in") or {})
    for f in dec["feasible_set"]:
        logged_est.setdefault(f["model_ref"], f["est_tokens_in"])
    est, missing = {}, []
    for mid, v in by_id.items():
        if text is not None:
            est[mid] = T.count_or_bound(text, v.get("tokenizer_ref"))[0]
        elif mid in logged_est:
            est[mid] = logged_est[mid]
        else:
            est[mid] = 0
            missing.append(mid)
    if missing:
        notes.append(f"no logged token estimate for {missing}; gate 3 replay is not meaningful "
                     f"for those variants (record predates the est_tokens_in field)")
    unverifiable = text is None

    exp_out = pol["objective"]["expected_output_tokens"].get(dec["task_class"], 0)
    for c in caps:
        c["_key"] = c.get("model_key") or X.capability_key(store, c.get("model_ref"))
    health = dec.get("health_snapshot") or {}
    runners = {
        "privacy": lambda cur: G.gate_privacy(cur, dec["privacy_class"], pol),
        "availability": lambda cur: G.gate_availability(cur, health.get("unhealthy", [])),
        "verifiability": lambda cur: G.gate_verifiability(cur, dec["task_class"], caps, pol),
        # free_ram_gb=None replays gate 3's RAM sub-check as non-excluding: the audit
        # cannot know past free memory. A decision that WAS excluded for RAM therefore
        # shows up as a replay mismatch, which is reported rather than silently passed.
        "context_fit": lambda cur: G.gate_context_fit(cur, est, exp_out, pol, None),
    }
    trace, cur = [], variants
    for gname in pol["gates"]["order"]:
        s, x, n = runners[gname](cur)
        trace.append({"gate": gname, "input_count": len(cur),
                      "survivors": [v["id"] for v in s], "excluded": x, "notes": list(notes)})
        cur = s
        if not cur:
            break
    return trace, cur, by_id, est, unverifiable, exp_out


def scan_corpus(store, run=None):
    """The text the proof-of-absence scan reads.

    run=None  -> the whole log: the STANDING STATE of what is in there today.
    run=<lbl> -> only that run's records, history entries and bindings: a REGRESSION TEST
                 of one policy.

    Both are needed, and they answer different questions. A policy change can stop future
    leakage without being able to retract past leakage: under append-only provenance the
    earlier text stays in data.jsonl and in the chained history forever, so the global scan
    keeps failing after the fix. Reporting only the scoped scan would hide that; reporting
    only the global one would hide whether the fix works. Measured on 2026-10-22 (exp2 v1
    leaked prompt p11; v2 stores no text, and the global scan still finds v1's copy).
    """
    d = store._dir("decision_log")
    if run is None:
        return {f"decision_log/{f}": (d / f).read_text() for f in LOG_FILES}
    ids = {r["id"] for r in store.records("decision_log") if r.get("run") == run}
    recs = [r for r in store.records("decision_log") if r["id"] in ids]
    hist = []
    for line in (d / "history.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        r = e.get("record") or {}
        if (r.get("id") in ids or (r.get("after") or {}).get("id") in ids
                or (r.get("target") or {}).get("record") in ids):
            hist.append(e)
    binds = [b for b in store.bindings("decision_log") if (b.get("target") or {}).get("record") in ids]
    return {"decision_log/data.jsonl": "\n".join(canon(r) for r in recs),
            "decision_log/history.jsonl": "\n".join(canon(h) for h in hist),
            "decision_log/bindings.jsonl": "\n".join(canon(b) for b in binds)}


def audit(store=None, run=None, needles=None, needle_labels=None, out=None,
          scan_run="__use_run__"):
    rep = Report()
    store = store or Store(ROOT)
    needles = needles or []
    needle_labels = needle_labels or [f"needle_{i}" for i in range(len(needles))]

    # ---------- 5. chain integrity (all five datasets) ----------
    for ds in DATASETS:
        ok, detail = store.verify_chain(ds)
        same, why = data_matches_history(store, ds) if ok else (False, "chain failed")
        rep.check(f"§6.1.5 chain verifies and data matches history: {ds}", ok and same,
                  f"chain={detail}; data={why}")

    decs = [d for d in store.records("decision_log") if run is None or d.get("run") == run]
    print(f"\n{len(decs)} decision record(s) in scope"
          + (f" (run={run})" if run else " (all runs)"))

    # ---------- 4. the connect list: prices, provenance, secrets, shape ----------
    # RC-1 retired the agent-curated catalog. Active records are the user's connections;
    # anything without a `connector` is a pre-RC row kept for historical replay only.
    active = [m for m in store.records("models", status="active") if "connector" in m]
    bad_price = [m["id"] for m in active
                 if m["price"]["input_per_mtok"] > PRICE_CEILING
                 or m["price"]["output_per_mtok"] > PRICE_CEILING
                 or m["price"]["input_per_mtok"] < 0
                 or m["price"]["output_per_mtok"] < 0]
    rep.check("§6.1.4 no declared price exceeds the §3.1 unit guard", not bad_price, bad_price)
    no_prov = [m["id"] for m in active
               if not m.get("declared_at") or not m.get("source_ref") or not m.get("label")]
    rep.check("§6.1.4 every active connection carries a user declaration (label, declared_at, "
              "source_ref)", not no_prov, no_prov)
    # A secret in a dataset is unrecoverable on a public repo, so the shape of `auth` is
    # checked directly: scheme plus the NAME of an environment variable, never a value.
    bad_auth = [m["id"] for m in active
                if set((m.get("auth") or {})) - {"scheme", "key_env", "header_name"}
                or ((m.get("auth") or {}).get("scheme") != "none"
                    and not (m.get("auth") or {}).get("key_env"))]
    rep.check("§6.1.4 no secret material in the registry: auth holds a scheme and an env-var "
              "name only", not bad_auth, bad_auth)
    bad_shape = [m["id"] for m in active
                 if not m.get("connector") or not m.get("base_url") or not m.get("model")
                 or not m.get("privacy_class") or not m.get("locus")]
    rep.check("§6.1.4 every active connection is routable in shape (connector, base_url, model, "
              "locus, privacy_class)", not bad_shape, bad_shape)

    # ---------- 3. eligibility recompute over every capability row ----------
    caps = store.records("capabilities")
    mism = []
    for c in caps:
        try:
            if G.recompute_eligible(c) != bool(c["eligible"]):
                mism.append(c["id"])
        except G.GateError as e:
            mism.append(f"{c['id']}: {e}")
    rep.check("§6.1.3 every stored `eligible` recomputes from its own snapshot",
              not mism, mism or f"{len(caps)} rows checked")

    # ---------- 6. reference resolution + quarantine ----------
    sup = {ds: {x["artifact"] for x in store.superseded(ds)} for ds in DATASETS}
    dangling, quarantined = [], []
    for b in store.bindings("decision_log"):
        if not b.get("sources"):
            dangling.append(f"{b['id']}: empty sources")
        for s in b["sources"]:
            ds = (s.get("dataset") or "").replace("ds:", "")
            if ds in DATASETS:
                if any(a in sup[ds] for a in (s.get("record"), s.get("filter"))):
                    quarantined.append(f"{b['id']} -> {s}")
                if s.get("record") and store.get(ds, s["record"]) is None:
                    dangling.append(f"{b['id']} -> {ds}:{s['record']}")
    rep.check("§6.1.6 every binding source resolves", not dangling, dangling)
    rep.check("§6.1.6 no binding sources from SUPERSEDED.json", not quarantined, quarantined)

    # ---------- 1, 2, 7, 8: per-decision replay ----------
    caps_keyed = []
    for c in store.records("capabilities"):
        c = dict(c)
        c["_key"] = c.get("model_key") or X.capability_key(store, c.get("model_ref"))
        caps_keyed.append(c)
    privacy_violations, replay_mismatch, choice_mismatch, cost_mismatch = [], [], [], []
    est_unverifiable = 0
    for d in decs:
        pol = store.get("policy", d["policy_ref"])
        if pol is None:
            replay_mismatch.append(f"{d['id']}: policy {d['policy_ref']} missing")
            continue
        trace, survivors, by_id, est, unverifiable, exp_out = replay(d, pol, store)

        # gate trace must match exactly, per gate, survivors and exclusions
        if len(trace) != len(d["gate_trace"]):
            replay_mismatch.append(f"{d['id']}: trace length {len(trace)} != {len(d['gate_trace'])}")
        else:
            for got, logged in zip(trace, d["gate_trace"]):
                if got["gate"] != logged["gate"] or got["survivors"] != logged["survivors"]:
                    replay_mismatch.append(
                        f"{d['id']} gate {logged['gate']}: survivors {got['survivors']} "
                        f"!= {logged['survivors']}")
                got_x = sorted((e["model_ref"], e["reason"]) for e in got["excluded"])
                log_x = sorted((e["model_ref"], e["reason"]) for e in logged["excluded"])
                if got_x != log_x:
                    replay_mismatch.append(f"{d['id']} gate {logged['gate']}: exclusions differ")

        # 2. choice recompute. In a cascade these are two different facts and both are
        # checked: the FIRST attempt must be the argmin of the feasible set (that is the
        # routing decision), and `chosen` must be the LAST attempt (that is the variant
        # whose answer was returned). Conflating them made every escalated decision look
        # like a routing error — caught by this audit on 2026-10-22.
        if d["outcome"] not in ("refused", "planned") and d.get("attempts"):
            measured = {c["_key"] for c in caps_keyed if c.get("prompt_class") == d["task_class"]}
            feas = [{"model_ref": v["id"],
                     "measured_known": (v.get("capability_key") or v.get("upstream_id")
                                        or v.get("model")) in measured,
                     "expected_cost_usd": expected_cost_usd(v, est.get(v["id"], 0), exp_out)}
                    for v in survivors]
            ordered = rank(feas, pol["objective"]["tie_break"]) if feas else []
            first = ordered[0]["model_ref"] if ordered else None
            if first != d["attempts"][0]["model_ref"]:
                choice_mismatch.append(f"{d['id']}: first attempt {d['attempts'][0]['model_ref']} "
                                       f"!= replayed argmin {first}")
            if d["chosen"]["model_ref"] != d["attempts"][-1]["model_ref"]:
                choice_mismatch.append(f"{d['id']}: chosen {d['chosen']['model_ref']} != final "
                                       f"attempt {d['attempts'][-1]['model_ref']}")
        elif d["outcome"] == "planned" and d.get("chosen"):
            measured = {c["_key"] for c in caps_keyed if c.get("prompt_class") == d["task_class"]}
            feas = [{"model_ref": v["id"],
                     "measured_known": (v.get("capability_key") or v.get("upstream_id")
                                        or v.get("model")) in measured,
                     "expected_cost_usd": expected_cost_usd(v, est.get(v["id"], 0), exp_out)}
                    for v in survivors]
            ordered = rank(feas, pol["objective"]["tie_break"]) if feas else []
            if not ordered or ordered[0]["model_ref"] != d["chosen"]["model_ref"]:
                choice_mismatch.append(
                    f"{d['id']}: planned {d['chosen']['model_ref']} but replay argmin is "
                    f"{ordered[0]['model_ref'] if ordered else 'EMPTY'}")

        # 7. privacy — compliance with the policy in force, not with an opinion of this layer.
        # Owner decision 2026-10-25: the user may permit cloud for private content. The
        # auditable facts are therefore (a) never route where that policy forbids, and
        # (b) whenever sensitive content does go to the cloud, a warning is on the record.
        if d["privacy_class"] in ("private", "confidential") and d.get("chosen"):
            m = by_id.get(d["chosen"]["model_ref"]) or store.get("models", d["chosen"]["model_ref"])
            if m and m["locus"] == "cloud":
                allowed = ((pol.get("privacy") or {}).get("allowed_loci") or {}).get(
                    d["privacy_class"], [])
                if "cloud" not in allowed:
                    privacy_violations.append(
                        f"{d['id']}: cloud not permitted for {d['privacy_class']} by {pol['id']}")
                elif not d.get("privacy_warning"):
                    privacy_violations.append(
                        f"{d['id']}: {d['privacy_class']} -> cloud with no recorded warning")

        # 8. cost actuals recompute from the pinned price
        for a in d.get("attempts") or []:
            m = by_id.get(a["model_ref"]) or store.get("models", a["model_ref"])
            if m is None:
                cost_mismatch.append(f"{d['id']}: attempt model {a['model_ref']} unresolvable")
                continue
            want = actual_cost_usd(m, a["tokens_in"], a["tokens_out"])
            got = a["actual_cost_usd"]
            # None is a legitimate value: an endpoint that reported no usage has no metered
            # cost, and RC-3.5 forbids filling that in with an estimate.
            if (want is None) != (got is None) or (want is not None and abs(want - got) > 1e-12):
                cost_mismatch.append(f"{d['id']}: logged ${got} != ${want} (meter="
                                     f"{a.get('meter')})")

        # schema-level privacy: no text for private requests
        if d["privacy_class"] == "private" and (d.get("prompt_text") or d.get("response_text")):
            privacy_violations.append(f"{d['id']}: text stored for a private request")
        if unverifiable:
            est_unverifiable += 1

    rep.check("§6.1.7 every sensitive route stays inside its policy, and every cloud route "
              "carries a recorded warning", not privacy_violations, privacy_violations)
    rep.check("§6.1.1 gate replay reproduces every logged gate_trace",
              not replay_mismatch, replay_mismatch)
    rep.check("§6.1.2 first attempt is the replayed argmin, and `chosen` is the final attempt",
              not choice_mismatch, choice_mismatch)
    rep.check("§6.1.8 every actual cost recomputes from tokens x pinned price",
              not cost_mismatch, cost_mismatch)

    # ---------- §6.2 proof-of-absence scan, at BOTH scopes ----------
    scope = run if scan_run == "__use_run__" else scan_run
    scanned = scan_corpus(store, scope)
    standing = scan_corpus(store, None)
    if needles:
        def find(corpus):
            hits = {}
            for label, needle in zip(needle_labels, needles):
                found = [k for k, txt in corpus.items() if needle and needle in txt]
                if found:
                    hits[label] = found
            return hits

        scoped_hits, standing_hits = find(scanned), find(standing)
        rep.check(f"§6.2 SCOPED to run={scope}: no private payload in this run's records",
                  not scoped_hits, scoped_hits)
        # A failure here is a permanent record of an earlier policy's leak, not a regression
        # of this run. Under append-only provenance it cannot be retracted (spec §10.11).
        rep.check("§6.2 STANDING log state: no private payload anywhere in the log today",
                  not standing_hits, standing_hits)
        rep.check("§6.2 scan covered all three log files at both scopes",
                  len(scanned) == 3 and len(standing) == 3, sorted(scanned))
        print(f"    scan scopes: run={scope} ({len(needles)} needles); "
              f"standing=whole log")
    else:
        print("    (§6.2 needle scan skipped: no battery supplied — the structural check "
              "above still applies)")

    # ---------- §6.3 coverage ----------
    n = len(decs) or 1
    pairs, unmeasured = set(), 0
    for d in decs:
        for g in d["gate_trace"]:
            for mid in g["survivors"]:
                pairs.add((mid, d["task_class"]))
    cap_pairs = {(c["model_ref"], c["prompt_class"]) for c in store.records("capabilities")}
    unmeasured = len(pairs - cap_pairs)
    bound = sum(1 for d in decs if d.get("binding_ref")
                and any(b["id"] == d["binding_ref"] for b in store.bindings("decision_log")))
    labelled = sum(1 for d in decs if d.get("classifier_matched_rank") is not None)
    cov = {
        "decisions": len(decs),
        "eligibility_coverage": round(1 - unmeasured / len(pairs), 4) if pairs else None,
        "unmeasured_variant_class_pairs": unmeasured,
        "binding_coverage": round(bound / n, 4),
        "privacy_label_coverage": round(labelled / n, 4),
        "gate3_token_estimate_unverifiable": est_unverifiable,
    }
    print("\n§6.3 coverage: " + json.dumps(cov, sort_keys=True))
    rep.check("§6.3 binding coverage is 100%", bound == len(decs), f"{bound}/{len(decs)}")

    report = {"run": run, "checks": rep.checks, "failures": rep.fails, "coverage": cov,
              "scan_scope": scope, "scanned_artifacts": sorted(scanned),
              "standing_scanned_artifacts": sorted(standing)}
    if out:
        Path(out).write_text(json.dumps(report, indent=1) + "\n")
        print(f"\nreport: {out}")
    print("\n" + "=" * 58)
    if rep.fails:
        print(f"AUDIT FAILED: {len(rep.fails)} of {len(rep.checks)} checks")
        return 1, report
    print(f"AUDIT OK — {len(rep.checks)} checks, all recomputed from frozen artifacts")
    return 0, report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    code, _ = audit(run=a.run, out=a.out)
    sys.exit(code)
