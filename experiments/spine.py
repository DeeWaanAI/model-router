#!/usr/bin/env python3
"""Spine script (Qafiya discipline): recomputes EVERY Layer-5 headline number from raw
frozen artifacts. If this script fails, the number does not go in the paper.

Exit 0 means the numbers RECOMPUTE and agree across independent paths. It does not mean
the results are good — a measured defect (a leak, a refusal rate, a violation) is a result
and is reported, not treated as a spine failure. The spine fails only on: battery drift,
disagreement between the raw run file and the frozen decision log, a binding whose
both_sides does not match recomputation, or an M1 gate violation (which SC1 declares a
code defect, not a finding).

Two independent paths must agree for every metric:
  path A — the experiment's raw file (results/raw/decisions_<run>.jsonl)
  path B — the frozen ds:decision_log records, read back through the store
"""
import hashlib, json, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
sys.path.insert(0, str(L5 / "router"))

import connections as X  # noqa: E402
from audit import audit, scan_corpus  # noqa: E402
from gates import recompute_eligible  # noqa: E402
from store import Store  # noqa: E402

CLASSES = ["private", "confidential", "public"]
fails = []
CHECKS = {"pass": 0, "fail": 0}


def check(label, got, want):
    ok = got == want
    CHECKS["pass" if ok else "fail"] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got} want={want}")
    if not ok:
        fails.append(label)


def truth(label, cond, detail=""):
    CHECKS["pass" if cond else "fail"] += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        fails.append(label)
    return cond


def load_battery(exp):
    man = json.loads((exp / "battery" / "manifest.json").read_text())
    raw = (exp / "battery" / "prompts.jsonl").read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != man["sha256"]:
        raise SystemExit(f"BATTERY DRIFT for {exp.name}: frozen {man['sha256'][:16]}… got {got[:16]}…")
    rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    return rows, man


def exp2_metrics(store, battery, man, run, policy_label):
    raw_path = HERE / "exp2" / "results" / "raw" / f"decisions_{run}.jsonl"
    if not raw_path.exists():
        raise SystemExit(f"missing raw artifact {raw_path}")
    raw = {r["battery_id"]: r for r in
           (json.loads(l) for l in raw_path.read_text().splitlines() if l.strip())}
    log = {d["id"]: d for d in store.records("decision_log") if d.get("run") == run}
    by_batt = {b["id"]: b for b in battery}

    print(f"\n=== exp2 run={run} policy={policy_label} ===")
    truth("raw file covers the whole frozen battery", len(raw) == man["n_prompts"],
          f"{len(raw)}/{man['n_prompts']}")
    truth("every raw row resolves to a frozen decision record",
          all(r["decision_ref"] in log for r in raw.values()))

    # ---- path A vs path B agreement, field by field ----
    disagree = []
    for bid, r in raw.items():
        d = log.get(r["decision_ref"])
        if d is None:
            disagree.append(f"{bid}: no log record")
            continue
        chosen = d.get("chosen") or {}
        m = store.get("models", chosen.get("model_ref")) if chosen.get("model_ref") else None
        for field, a, b in (("classified", r["classified"], d["privacy_class"]),
                            ("outcome", r["outcome"], d["outcome"]),
                            ("matched_rank", r["classifier_matched_rank"], d["classifier_matched_rank"]),
                            ("locus", r["chosen_locus"], m["locus"] if m else None),
                            ("text_stored", r["prompt_text_stored"], d["prompt_text"] is not None)):
            if a != b:
                disagree.append(f"{bid}.{field}: raw={a} log={b}")
    truth("raw run file and frozen decision log agree on every field", not disagree,
          "; ".join(disagree[:4]))

    # ---- M1 gate compliance: classified-private -> cloud ----
    cls_priv = [bid for bid, r in raw.items() if r["classified"] == "private"]
    m1 = [bid for bid in cls_priv if raw[bid]["chosen_locus"] == "cloud"]
    # ---- M2 end-to-end: LABELLED private -> cloud ----
    lab_priv = [b["id"] for b in battery if b["label"] == "private"]
    m2 = [bid for bid in lab_priv if raw[bid]["chosen_locus"] == "cloud"]

    # ---- M3 confusion matrix + accuracy ----
    conf = defaultdict(Counter)
    for b in battery:
        conf[b["label"]][raw[b["id"]]["classified"]] += 1
    correct = sum(conf[c][c] for c in CLASSES)
    n = len(battery)

    # ---- M4 over / M5 under ----
    order = {"public": 0, "confidential": 1, "private": 2}
    over = [b["id"] for b in battery
            if order[raw[b["id"]]["classified"]] > order[b["label"]]]
    under = [b["id"] for b in battery
             if order[raw[b["id"]]["classified"]] < order[b["label"]]]

    # ---- M6 privacy-label coverage ----
    defaulted = [b["id"] for b in battery if raw[b["id"]]["classifier_matched_rank"] is None]

    # ---- M7 absence scans, both scopes, both needle sets ----
    needles_cls = [(bid, by_batt[bid]["text"]) for bid in cls_priv]
    needles_lab = [(bid, by_batt[bid]["text"]) for bid in lab_priv]

    def scan(needles, scope_run):
        corpus = scan_corpus(store, scope_run)
        hits = {}
        for bid, text in needles:
            found = sorted(k for k, body in corpus.items() if text and text in body)
            if found:
                hits[bid] = found
        return hits

    m7 = {
        "classified_private_scoped": scan(needles_cls, run),
        "classified_private_standing": scan(needles_cls, None),
        "labelled_private_scoped": scan(needles_lab, run),
        "labelled_private_standing": scan(needles_lab, None),
    }
    text_stored = [bid for bid, r in raw.items() if r["prompt_text_stored"]]

    # ---- M8 refusals ----
    refusals = Counter(r["refusal_reason"] for r in raw.values() if r["outcome"] == "refused")

    metrics = {
        "run": run, "policy_label": policy_label, "n_prompts": n,
        "battery_sha256": man["sha256"],
        "M1_gate_violations": len(m1), "M1_classified_private": len(cls_priv),
        "M1_violating_ids": m1,
        "M2_end_to_end_violations": len(m2), "M2_labelled_private": len(lab_priv),
        "M2_violating_ids": m2,
        "M3_accuracy": round(correct / n, 4), "M3_correct": correct,
        "M3_confusion": {k: dict(v) for k, v in sorted(conf.items())},
        "M4_over_classified": len(over), "M4_rate": round(len(over) / n, 4), "M4_ids": over,
        "M5_under_classified": len(under), "M5_rate": round(len(under) / n, 4), "M5_ids": under,
        "M6_label_coverage": round(1 - len(defaulted) / n, 4),
        "M6_defaulted_ids": defaulted,
        "M7_leaks": {k: v for k, v in m7.items() if v},
        "M7_scanned_needle_sets": {"classified_private": len(needles_cls),
                                   "labelled_private": len(needles_lab)},
        "M8_refusals": dict(refusals),
        "M8_refusal_rate": round(sum(refusals.values()) / n, 4),
        "decisions_with_text_stored": len(text_stored),
        "text_stored_ids": text_stored,
        "classifications": {b["id"]: raw[b["id"]]["classified"] for b in battery},
    }

    print(f"  M1 gate violations          : {len(m1)} / {len(cls_priv)} classified-private")
    print(f"  M2 end-to-end violations    : {len(m2)} / {len(lab_priv)} labelled-private")
    print(f"  M3 classifier accuracy      : {metrics['M3_accuracy']} ({correct}/{n})")
    for c in CLASSES:
        print(f"       {c:13} -> {dict(conf[c])}")
    print(f"  M4 over-classified (fail-closed cost): {len(over)} ({metrics['M4_rate']}) {over}")
    print(f"  M5 UNDER-classified (dangerous)      : {len(under)} ({metrics['M5_rate']}) {under}")
    print(f"  M6 privacy-label coverage   : {metrics['M6_label_coverage']} "
          f"(defaulted: {defaulted})")
    print(f"  M7 leaks  scoped : classified={sorted(m7['classified_private_scoped'])} "
          f"labelled={sorted(m7['labelled_private_scoped'])}")
    print(f"     leaks  standing: classified={sorted(m7['classified_private_standing'])} "
          f"labelled={sorted(m7['labelled_private_standing'])}")
    print(f"  M8 refusals                 : {dict(refusals) or 'none'}")
    print(f"  decisions storing prompt text: {len(text_stored)}/{n}")

    # ---- SC1: an M1 violation is a code defect, so it fails the spine ----
    truth(f"SC1 M1 gate compliance is 0 violations (run={run})", len(m1) == 0, m1)

    # ---- the aggregate binding must agree with recomputation ----
    binds = [b for b in store.bindings("decision_log")
             if (b.get("target") or {}).get("scope") == f"run={run}"]
    if binds:
        agg = binds[-1]
        check(f"aggregate binding value == recomputed M1 (run={run})",
              agg["value"], len(m1))
        check(f"aggregate binding both_sides.private_requests == classified-private count",
              agg["assertion"]["both_sides"]["private_requests"], len(cls_priv))
    else:
        truth(f"aggregate binding exists for run={run}", False)

    return metrics


def exp13_metrics(store):
    """Experiments 1 and 3, recomputed from exp1's frozen raw artifacts and the frozen
    capability rows. Forced arms are controls, so they live in results/raw, not the log."""
    print("\n=== exp1 / exp3 ===")
    raw = HERE / "exp1" / "results" / "raw"
    # Which pilot to verify is READ from the registry, not guessed from filenames: every active
    # ds:capabilities row names the artifact its numbers came from. "Newest file on disk" is a
    # proxy that already picked the wrong roll once (v1 sorts after v3 when the forced-arm
    # prefix is included), and a spine that verifies the wrong artifact is worse than none.
    caps_now = store.records("capabilities", status="active")
    srcs = sorted({c["measured"]["source_run"] for c in caps_now})
    if len(srcs) != 1:
        raise SystemExit(f"spine: active capability rows cite {len(srcs)} different source "
                         f"runs — the evidence base is not one frozen roll: {srcs}")
    stem, fname = srcs[0].split("/", 1)
    # a capability row's source may be a frozen raw roll OR an amended sidecar of one
    fpath = next((p for p in (raw / fname, HERE / "exp1" / "results" / "derived" / fname)
                  if p.exists()), None)
    if fpath is None:
        raise SystemExit(f"spine: capability rows cite {srcs[0]} but {fname} is on neither "
                         f"{raw} nor results/derived")
    print(f"  measurement source: {fpath.relative_to(L5)}")
    rpath = next((p for p in (HERE / "exp1" / "results" / "derived" / f"routed_{stem}-regraded.jsonl",
                              raw / f"routed_{stem}.jsonl", raw / f"routed_arm_{stem}.jsonl")
                  if p.exists()), None)
    mpath = raw / f"run_meta_{stem}.json"
    if rpath is None or not mpath.exists():
        raise SystemExit(f"spine: incomplete artifact set for run '{stem}' "
                         f"(missing {rpath or mpath.name})")
    print(f"  verifying pilot run cited by ds:capabilities: {stem} ({fname})")
    forced = [json.loads(l) for l in fpath.read_text().splitlines() if l.strip()]
    routed = [json.loads(l) for l in rpath.read_text().splitlines() if l.strip()]
    meta = json.loads(mpath.read_text())

    runs = defaultdict(list)
    for r in forced:
        runs[(r["arm"], r.get("model") or r.get("status", "unknown"))].append(r)

    out = {"arms": {}, "routed": {}, "preflight_estimate_usd": meta["preflight_estimate_usd"],
           "actual_spend_usd": meta["ACTUAL_SPEND_USD"]}
    print(f"  spend: pre-flight estimate ${meta['preflight_estimate_usd']:.4f} "
          f"(conservative: full output budget), actual ${meta['ACTUAL_SPEND_USD']:.6f}")

    for (arm, model), rs in sorted(runs.items()):
        meas = [r for r in rs if r.get("quality") is not None]
        cost = sum(r["cost_usd"] for r in meas)
        q = statistics.fmean(r["quality"] for r in meas) if meas else None
        correct = sum(r["fields_correct"] for r in meas)
        total = sum(r["fields_total"] for r in meas)
        out["arms"][arm] = {"model": model, "n_runs": len(rs), "n_measurable": len(meas),
                            "n_skipped_context": sum(1 for r in rs if r.get("status") == "excluded_context"),
                            "cost_usd": round(cost, 6), "quality_mean": round(q, 4) if q else q,
                            "fields_correct": correct, "fields_total": total,
                            "termination": round(statistics.fmean(1.0 if r.get("terminated") else 0.0
                                                                  for r in meas), 4) if meas else None,
                            "schema": round(statistics.fmean(1.0 if r.get("schema_compliance") else 0.0
                                                             for r in meas), 4) if meas else None,
                            "usd_per_correct_field": (round(cost / correct, 6) if correct else None)}
        print(f"  {arm:20} n={len(rs)} measurable={len(meas)} skipped_ctx="
              f"{out['arms'][arm]['n_skipped_context']} quality={out['arms'][arm]['quality_mean']} "
              f"${cost:.5f}")

    rcost = sum(r.get("cost_usd") or 0.0 for r in routed)
    graded = [r for r in routed if r.get("fields_total")]
    rc = sum(r.get("fields_correct", 0) for r in graded)
    rt = sum(r.get("fields_total", 0) for r in graded)
    out["routed"] = {"n_items": len(routed), "n_graded": len(graded),
                     "cost_usd": round(rcost, 6),
                     "fields_correct": rc, "fields_total": rt,
                     "quality_mean": round(rc / rt, 4) if rt else None,
                     "loci": sorted({str(r["locus"]) for r in routed}),
                     "models": sorted({str(r["model"]) for r in routed}),
                     "outcomes": dict(Counter(r["outcome"] for r in routed)),
                     "all_bound": all(r["binding_ref"] for r in routed)}
    print(f"  ROUTED               n={len(routed)} quality={out['routed']['quality_mean']} "
          f"${rcost:.5f} models={out['routed']['models']}")
    truth("routed arm: every item has a bound decision record", out["routed"]["all_bound"])

    # The decisions that really spent money get the same replay treatment as the $0 ones:
    # §6.1.8 re-derives each actual cost from tokens x the price pinned at decision time.
    pcode, prep = audit(store=store, run=stem, needles=[], needle_labels=[],
                        out=str(HERE / "exp1" / "results" / "raw" / f"audit_{stem}.json"))
    truth(f"audit(run={stem}) exits 0 over the paid decisions", pcode == 0, prep["failures"])
    out["audit"] = {"exit": pcode, "failures": prep["failures"], "coverage": prep["coverage"]}
    print(f"  audit: exit={pcode} coverage={prep['coverage']}")

    # F1/F3: the routed arm must not cost more than always-big, and must not be worse
    big = out["arms"].get("always-big", {})
    if big.get("cost_usd") is not None:
        truth("H1: routed costs strictly less than always-big",
              out["routed"]["cost_usd"] < big["cost_usd"],
              (out["routed"]["cost_usd"], big["cost_usd"]))
        if out["routed"]["quality_mean"] is not None and big.get("quality_mean") is not None:
            print(f"  quality: routed={out['routed']['quality_mean']} always-big={big['quality_mean']} "
                  f"(reported either way; a loss is a result)")

    # H3, restated for battery v2's two architectures: can any arm get the 800-row total right
    # in context, and can it get it right by writing a program? Amendment B.1 is what makes this
    # question answerable — under the frozen grader every arm looked half-wrong either way.
    haz = [r for r in forced if r.get("item") == "b_total_large_code" and r.get("quality") is not None]
    inhead = [r for r in forced if r.get("item") == "b_total_large_inhead"
              and r.get("quality") is not None]
    if haz:
        code_pairs = ", ".join(f"{r['model']}={r['quality']}" for r in haz)
        print(f"  H3 800-row total, VIA CODE : {code_pairs} "
              f"arms at full credit = {sorted({r['model'] for r in haz if r['quality'] == 1.0})}")
    if inhead:
        ih_pairs = ", ".join(f"{r['model']}={r['quality']}" for r in inhead)
        print(f"  H3 800-row total, IN CONTEXT: {ih_pairs} "
              f"arms at full credit = {sorted({r['model'] for r in inhead if r['quality'] == 1.0})}")
    if haz and inhead:
        truth("H3 as amended: writing and running a program clears the aggregation item that no "
              "arm clears in context — the layer's central architectural claim, and it was hidden "
              "by the pre-B.1 grader",
              len({r["model"] for r in haz if r["quality"] == 1.0}) > 0
              and not any(r["quality"] == 1.0 for r in inhead),
              {"code_full_credit": sorted({r["model"] for r in haz if r["quality"] == 1.0}),
               "inhead_full_credit": sorted({r["model"] for r in inhead if r["quality"] == 1.0})})
    # H4: the reasoning arm hitting its budget
    hz = [r for r in forced if r.get("arm") == "hazard-reasoning"]
    if hz:
        lengths = [r["finish_reason"] for r in hz]
        print(f"  H4 reasoning arm finish_reasons={lengths}")
        truth("H4: the reasoning arm hits its token budget (the hazard is measurable)",
              any(x == "length" for x in lengths), lengths)

    # capability rows: recompute eligibility from the row's own snapshot (§6.1.3)
    caps = store.records("capabilities", status="active")
    bad = [c["id"] for c in caps if recompute_eligible(c) != bool(c["eligible"])]
    truth("every ds:capabilities row's `eligible` recomputes from its own thresholds_snapshot",
          not bad, bad)
    print(f"  capability rows: {len(caps)}")
    if caps:
        truth("capability rows carry a source_run pointing at frozen artifacts",
              all(c["measured"].get("source_run") for c in caps))
        truth("capability rows declare which grader produced them (amendment B.1)",
              all(c["measured"].get("grader") for c in caps),
              sorted({c["model_ref"] for c in caps if not c["measured"].get("grader")}))

    # exp1 -> exp3 link: the registry's accuracy must equal what the cited rows say, recomputed
    # here. A row that agrees with its own snapshot but not with its source is still a lie.
    src = defaultdict(list)
    for r in forced:
        src[(r.get("model_ref"), r["prompt_class"])].append(r)
    drift = []
    for c in caps:
        rs = [r for r in src.get((c["model_ref"], c["prompt_class"]), [])
              if r.get("quality") is not None]
        if len(rs) != c["n"]:
            drift.append({"id": c["id"], "why": f"n={c['n']} but {len(rs)} source rows"})
            continue
        acc = round(statistics.fmean(r["quality"] for r in rs), 4)
        if acc != c["measured"]["accuracy"]:
            drift.append({"id": c["id"], "why": f"stored {c['measured']['accuracy']} vs {acc}"})
    truth("each ds:capabilities accuracy equals the mean of the quality scores it cites",
          not drift, drift)

    # Amendment B.1 verified, not asserted: the sidecar must differ from the frozen roll on
    # exactly the rows the regrade reported, and on nothing else.
    frozen_raw = raw / f"arms_{stem}.jsonl"
    if fpath != frozen_raw and frozen_raw.exists():
        orig = {(r["item"], r["arm"]): r for r in
                (json.loads(l) for l in frozen_raw.read_text().splitlines() if l.strip())}
        moved = [(k, orig[k]["quality"], r["quality"]) for r in forced
                 if (k := (r["item"], r["arm"])) in orig
                 and orig[k]["quality"] != r["quality"]]
        reg = json.loads((HERE / "exp1" / "results" / "derived" / f"regrade_{stem}.json").read_text())
        print(f"  amendment B.1: {len(moved)} of {len(forced)} rows rescored "
              f"(regrade.py reported {reg['n_scores_corrected']})")
        truth("the number of rescored rows matches the amendment record",
              len(moved) == reg["n_scores_corrected"], (len(moved), reg["n_scores_corrected"]))
        truth("every rescored row belongs to an item the amendment names as affected",
              {m[0][0] for m in moved} <= set(reg["affected_items"]),
              sorted({m[0][0] for m in moved} - set(reg["affected_items"])))
        out["amendment_b1"] = {"n_rows_rescored": len(moved),
                               "affected_items": sorted(reg["affected_items"]),
                               "alias_map": reg["alias_map"],
                               "rescored": [{"item": m[0][0], "arm": m[0][1],
                                             "frozen_grader": m[1], "b1_grader": m[2]}
                                            for m in sorted(moved)]}
        truth("the routed arm's corrected quality equals the amendment record's",
              out["routed"]["quality_mean"] == reg["routed_arm"]["quality"],
              (out["routed"]["quality_mean"], reg["routed_arm"]["quality"]))

    out["capabilities"] = {"n_rows": len(caps),
                           "ineligible": sorted(c["id"] for c in caps if not c["eligible"]),
                           "eligible": sorted(c["id"] for c in caps if c["eligible"])}
    return out


def exp3_sweep_metrics(store, sweep_run):
    """Recompute the eligibility sweep from the frozen decision log — never from the sweep
    script's own printout.

    Path A is `exp3/results/derived/sweep_<run>.json`, written by sweep_eligibility.py from the
    values it read out of the log. Path B is this function, which re-reads the same decision
    records independently. If the sweep script had a bug in its accumulation, the two disagree.
    """
    print("\n=== exp3 step 2: eligibility sweep ===")
    fp = HERE / "exp3" / "results" / "derived" / f"sweep_{sweep_run}.json"
    if not fp.exists():
        raise SystemExit(f"spine: no frozen sweep artifact at {fp}")
    doc = json.loads(fp.read_text())
    points = doc["points"]

    bat_dir = HERE / "exp1" / "battery"
    man2 = json.loads((bat_dir / "manifest_v2.json").read_text())
    bat_bytes = (bat_dir / man2["items"]).read_bytes()
    got = hashlib.sha256(bat_bytes).hexdigest()
    truth(f"sweep battery {man2['items']} unmodified", got == man2["items_sha256"], got[:16])
    items = [json.loads(l) for l in bat_bytes.decode().splitlines() if l.strip()]
    n_compute = sum(1 for it in items if it["prompt_class"] == "compute_via_code")

    models = {m["id"]: m for m in store.records("models")}
    caps = store.records("capabilities", status="active")
    acc_by_cell = {(c["model_ref"], c["prompt_class"]): c["measured"]["accuracy"] for c in caps}
    decisions = [d for d in store.records("decision_log", status="active") if d.get("run") == sweep_run]
    print(f"  {len(points)} points x {len(items)} items; {len(decisions)} decision records in the log")

    by_policy = defaultdict(list)
    for d in decisions:
        by_policy[d["policy_ref"]].append(d)

    recomputed, empty_sets = [], 0
    for pt in points:
        ds = by_policy.get(pt["policy_id"], [])
        tiebreak = store.get("policy", pt["policy_id"])["objective"]["tie_break"]
        chosen, refused, cost, accs, unm = [], 0, 0.0, [], 0
        for d in ds:
            if d["outcome"] == "refused":
                refused += 1
                continue
            if d["outcome"] != "planned":
                truth(f"outcome of a sweep decision is 'planned' (no model ran): {d['id']}", False,
                      d["outcome"])
            fs = d["feasible_set"]
            if not fs:
                empty_sets += 1
                continue
            mref = d["chosen"]["model_ref"]
            # §6.1.2, recomputed independently: the logged set is in rank order and the
            # committed choice is its first element.
            if tiebreak != "id_asc":
                raise SystemExit(f"spine: cost.rank only supports id_asc, log says {tiebreak}")
            if fs != sorted(fs, key=lambda f: (f["expected_cost_usd"], f["model_ref"])):
                truth(f"feasible_set is in rank order for {d['id']}", False)
            if mref != fs[0]["model_ref"]:
                truth(f"chosen == argmin for {d['id']}", False, (mref, fs[0]["model_ref"]))
            chosen.append(models[mref]["name"])
            cost += fs[0]["expected_cost_usd"]
            a = acc_by_cell.get((mref, d["task_class"]))
            if a is None:
                unm += 1
            else:
                accs.append(a)
        recomputed.append({
            "policy_id": pt["policy_id"], "mode": pt["mode"], "min_accuracy": pt["min_accuracy"],
            "min_schema": pt["min_schema"], "n_decisions": len(ds), "refused": refused,
            "chosen_counts": {k: chosen.count(k) for k in sorted(set(chosen))},
            "mean_measured_accuracy": round(statistics.fmean(accs), 4) if accs else None,
            "n_choices_unmeasured": unm, "est_cost_usd": round(cost, 6)})

    agree = []
    for a, b in zip(points, recomputed):
        keys = ("refused", "chosen_counts", "mean_measured_accuracy", "n_choices_unmeasured",
                "est_cost_usd")
        bad = [k for k in keys if a[k] != b[k]]
        if bad:
            agree.append((a["policy_id"], bad, {k: (a[k], b[k]) for k in bad}))
    truth("sweep printout agrees with the decision log on all 5 accumulation fields at every "
          f"point ({len(points)} points)", not agree, agree[:2])
    # Every grid point × every compute item has exactly one decision, and no decision belongs
    # to a request the sweep never made. request_ref is reconstructed here, not read from the
    # sweep script, so a silent item-selection bug in the sweep would show up as a mismatch.
    want_refs = {f"req_{sweep_run}_sweep-{p['mode']}-acc{p['min_accuracy']}"
                 f"-sch{p['min_schema']}_{it['id']}"
                 for p in points for it in items if it["prompt_class"] == "compute_via_code"}
    got_refs = {d["request_ref"] for d in decisions}
    truth(f"the sweep logged exactly one decision per grid point × {n_compute} compute items "
          f"({len(want_refs)} expected)",
          got_refs == want_refs,
          {"missing": sorted(want_refs - got_refs)[:3], "extra": sorted(got_refs - want_refs)[:3]})
    truth(f"the sweep made {len(points) * n_compute} decisions and ran 0 model calls (cost $0)",
          len(decisions) == len(points) * n_compute and
          all(d["outcome"] in ("planned", "refused") for d in decisions),
          Counter(d["outcome"] for d in decisions))

    # non-vacuity: a policy sweep that never changes the winner measured nothing
    winners = {json.dumps(r["chosen_counts"], sort_keys=True) for r in recomputed}
    truth("the sweep is non-vacuous: distinct thresholds produce distinct winner sets",
          len(winners) >= 3, sorted(winners))

    def point(acc, sch, mode="allow_flagged"):
        return next(r for r in recomputed
                    if r["min_accuracy"] == acc and r["min_schema"] == sch and r["mode"] == mode)

    loose, strict = point(0.0, 0.0), point(0.8, 0.9)
    floor_winner = list(strict["chosen_counts"])[0]
    # The floor's price, item by item. Averaging a per-item minimum against a per-item winner
    # would mix two different prompt lengths, so each request is compared only to itself — and
    # the join is on the BATTERY ITEM, not on request_ref, because request_ref embeds the policy
    # label and so differs between the two points being compared.
    def item_of(ref):
        return next((it["id"] for it in items if ref.endswith("_" + it["id"])), None)

    cheap_cloud_by_item, strict_cost_by_item = {}, {}
    for d in by_policy[loose["policy_id"]]:
        cc = [f["expected_cost_usd"] for f in d["feasible_set"]
              if models[f["model_ref"]]["locus"] == "cloud" and f["expected_cost_usd"] > 0]
        if cc:
            cheap_cloud_by_item[item_of(d["request_ref"])] = min(cc)
    for d in by_policy[strict["policy_id"]]:
        if d["outcome"] != "refused" and d["feasible_set"]:
            strict_cost_by_item[item_of(d["request_ref"])] = d["feasible_set"][0]["expected_cost_usd"]
    common = [k for k in strict_cost_by_item if k in cheap_cloud_by_item]
    truth("every compute item appears in both policy points under its own id",
          None not in common and len(common) == n_compute,
          {"strict": sorted(k for k in strict_cost_by_item if k),
           "loose": sorted(k for k in cheap_cloud_by_item if k)})
    ratios = sorted(strict_cost_by_item[k] / cheap_cloud_by_item[k] for k in common)
    price_ratio = round(statistics.fmean(ratios), 2) if ratios else None
    print(f"  no floor      : {loose['chosen_counts']} mean_measured_acc="
          f"{loose['mean_measured_accuracy']} ${loose['est_cost_usd']:.6f} for {n_compute} items")
    print(f"  shipped floor : {strict['chosen_counts']} mean_measured_acc="
          f"{strict['mean_measured_accuracy']} ${strict['est_cost_usd']:.6f}")
    if ratios:
        print(f"  the floor's price vs the cheapest MEASURED cloud arm on the same items: "
              f"{ratios[0]:.1f}x–{ratios[-1]:.1f}x (mean {price_ratio}x, n={len(ratios)}); "
              f"vs the no-floor winner the ratio is undefined — that arm is priced $0")
    n_eligible = sum(1 for r in recomputed if r["mode"] == "allow_flagged"
                     and len(r["chosen_counts"]) == 1
                     and list(r["chosen_counts"])[0] == floor_winner)
    print(f"  {n_eligible}/{len([r for r in recomputed if r['mode'] == 'allow_flagged'])} "
          f"allow_flagged points converge on the same winner as the shipped default")

    # ---- coverage, and what the missing cell actually costs ----
    # Roll 1 of this sweep (exp3-sweep) ran with only 3 of 6 arms measured, so its interesting
    # column was "the gate fell through to an arm it knew nothing about". Roll 2 has 5 of 6.
    # The one still-unmeasured arm is named here rather than averaged away.
    # Coverage is a property of the RUN, not of today's registry: the connect list changes
    # (providers retire models, users add them), and the question this number answers is
    # "of the arms this run considered, how many had a measurement". Computing it from the
    # current list made the check fail the day a provider retired an arm that the run used.
    pairs = {(mid, d["task_class"]) for d in decisions
             for g in d["gate_trace"] for mid in g["survivors"]}
    cap_pairs = {(c["model_ref"], c["prompt_class"]) for c in caps}
    unmeasured_pairs = pairs - cap_pairs
    coverage = round(1 - len(unmeasured_pairs) / len(pairs), 4) if pairs else None
    arm_names = {m["id"]: (m.get("label") or m.get("name")) for m in models.values()}
    unmeasured_arms = sorted({arm_names.get(mid, mid) for mid, _cls in unmeasured_pairs})
    with_row_n = len(pairs) - len(unmeasured_pairs)
    print(f"  capability coverage for compute_via_code: {with_row_n}/{len(pairs)} "
          f"arms considered by the run = {coverage}; unmeasured: {unmeasured_arms}")
    truth("no sweep point chose an arm with no measurement",
          all(r["n_choices_unmeasured"] == 0 for r in recomputed),
          {r["policy_id"]: r["n_choices_unmeasured"] for r in recomputed
           if r["n_choices_unmeasured"]})

    # The two missing cells are the SAME model under a different name: the reasoning variant is
    # priced identically and loses the id_asc tie every time, so the mode knob changes the
    # candidate set without changing one routing outcome. That is a result, and the opposite
    # claim is what would be worth reporting — so both halves are checked here.
    cand_by_mode = defaultdict(set)
    for r in recomputed:
        for d in by_policy[r["policy_id"]]:
            cand_by_mode[r["mode"]] |= {models[f["model_ref"]]["name"] for f in d["feasible_set"]}
    only_in_loose = cand_by_mode["allow_flagged"] - cand_by_mode["refuse"]
    print(f"  candidate sets: allow_flagged sees {sorted(cand_by_mode['allow_flagged'])}")
    print(f"                  refuse sees {sorted(cand_by_mode['refuse'])} "
          f"-> mode removes {sorted(only_in_loose) or 'nothing'}")
    truth("missing_measurement mode DOES change which arms are candidates",
          bool(only_in_loose), sorted(only_in_loose))
    outcome_diffs = [(r["min_accuracy"], r["min_schema"])
                     for r in recomputed if r["mode"] == "refuse"
                     and point(r["min_accuracy"], r["min_schema"])["chosen_counts"] != r["chosen_counts"]]
    print(f"  missing_measurement mode changes {len(outcome_diffs)} of {len(points) // 2} "
          f"routing outcomes (the unmeasured arm never wins): {outcome_diffs or 'none'}")
    truth("the unmeasured arm never wins, so the mode knob is outcome-neutral on this matrix",
          not outcome_diffs, outcome_diffs)

    truth("no decision reports a winner while carrying an empty feasible set",
          empty_sets == 0, empty_sets)

    # Which knob is actually paying? Release one floor at a time and read the winner and the cost.
    # This is the number a owner needs: the two thresholds are not equal levers.
    knob_price = []
    for name, a, s in (("shipped: acc>=0.8 and schema>=0.9", 0.8, 0.9),
                       ("accuracy floor only (schema released)", 0.8, 0.0),
                       ("schema floor only (accuracy released)", 0.0, 0.9),
                       ("neither floor", 0.0, 0.0)):
        p = point(a, s)
        knob_price.append({"policy": name, "min_accuracy": a, "min_schema": s,
                           "winner": p["chosen_counts"], "mean_measured_accuracy":
                           p["mean_measured_accuracy"], "est_cost_usd": p["est_cost_usd"]})
        print(f"  {name:42} -> {p['chosen_counts']} acc={p['mean_measured_accuracy']} "
              f"${p['est_cost_usd']:.6f}")
    truth("the two floors are not equal levers: exactly one of them buys the 2x jump",
          len({k["est_cost_usd"] for k in knob_price}) >= 3,
          [(k["policy"], k["est_cost_usd"]) for k in knob_price])

    # The replay audit over the sweep's own 144 decisions: gate traces, argmin, binding
    # completeness, and coverage, all recomputed from the log rather than from this script's
    # reading of it.
    acode, arep = audit(store=store, run=sweep_run, needles=[], needle_labels=[],
                        out=str(HERE / "exp3" / "results" / "raw" / f"audit_{sweep_run}.json"))
    truth(f"audit(run={sweep_run}) exits 0 over {len(decisions)} logged decisions",
          acode == 0, arep["failures"])
    coverage_audited = arep["coverage"]["eligibility_coverage"]
    truth("the coverage this script computes equals the audit's (one definition, two paths)",
          coverage_audited == coverage, (coverage_audited, coverage))

    return {"run": sweep_run, "source_measurements": doc["source_measurements"],
            "n_points": len(points), "n_decisions": len(decisions),
            "cost_usd": 0.0, "points": recomputed,
            "no_floor": loose, "shipped_default": strict,
            "cheapest_cloud_usd_per_item": {k: v for k, v in sorted(cheap_cloud_by_item.items())},
            "floor_price_ratio_mean": price_ratio,
            "floor_price_ratio_range": [ratios[0], ratios[-1]] if ratios else None,
            "floor_winner": floor_winner,
            "knob_price": knob_price,
            "coverage_compute_via_code": coverage, "unmeasured_arms": unmeasured_arms,
            "mode_removes_candidates": sorted(only_in_loose),
            "mode_changes_outcomes_at": outcome_diffs,
            "audit": {"exit": acode, "failures": arep["failures"], "coverage": arep["coverage"]},
            "distinct_winner_sets": len(winners)}



def derived_artifacts(all_metrics):
    """The three paper tables produced outside this script still have to be true.

    They are recomputation paths themselves (each reads frozen artifacts), so the spine does
    not re-run them; it checks that their headline assertions hold and that the files exist.
    """
    print("\n=== derived experiments ===")
    d = HERE / "results" / "derived"
    fi = d / "fault_injection.json"
    if fi.exists():
        r = json.loads(fi.read_text())
        truth("fault injection: every injected fault is caught on its expected check",
              r["detected_on_expected_check"] == f"{len(r['mutations'])}/{len(r['mutations'])}",
              r["detected_on_expected_check"])
        truth("fault injection: zero false positives on unmodified copies",
              r["false_positives"].startswith("0/"), r["false_positives"])
        print(f"  mutations={len(r['mutations'])} clean_controls={len(r['clean_controls'])}")
    dg = d / "diagnostics.json"
    if dg.exists():
        r = json.loads(dg.read_text())
        truth("determinism: identical frozen inputs produce identical decisions",
              r["determinism"]["bytes_equal_except_volatile"],
              r["determinism"]["fields_differing"])
        for g in r["gates"]["overall"]:
            print(f"  gate {g['gate']:14} runs={g['decisions']:4} bind_rate={g['binding_rate']}")
    bl = d / "baselines.json"
    if bl.exists():
        r = json.loads(bl.read_text())
        rows = {x["strategy"]: x for x in r["panels"]["B_compute_6_items"]}
        free = rows.get("always-local-8b")
        if free:
            truth("baselines: the free local 8B is not dominated (>=0.70 accuracy at $0)",
                  free["accuracy"] >= 0.70, free["accuracy"])
        after = rows.get("router, default policy (v9)")
        if after:
            truth("baselines: the default policy's expected accuracy on the six compute items "
                  "is 1.0", after["accuracy"] == 1.0, after["accuracy"])
        print(f"  baseline strategies compared: {len(r['panels']['B_compute_6_items'])}")

    reps = HERE / "exp1" / "results" / "raw" / "reps_exp1-reps-v1.jsonl"
    reps_sum = HERE / "exp1" / "results" / "raw" / "summary_exp1-reps-v1.json"
    reps_meta = HERE / "exp1" / "results" / "raw" / "run_meta_exp1-reps-v1.json"
    if reps.exists() and reps_sum.exists():
        rows = [json.loads(l) for l in reps.read_text().splitlines() if l.strip()]
        summ = json.loads(reps_sum.read_text())
        meta = json.loads(reps_meta.read_text())
        bad = []
        for key, cell in summ["cells"].items():
            arm, pcls = key.split("|")
            rs = [r for r in rows if r["arm"] == arm and r["prompt_class"] == pcls
                  and r.get("quality") is not None]
            mean = round(statistics.fmean(r["quality"] for r in rs), 4) if rs else None
            per_rep = {}
            for rep in range(1, meta["reps"] + 1):
                xs = [r["quality"] for r in rs if r.get("rep") == rep]
                if xs:
                    per_rep[rep] = round(statistics.fmean(xs), 4)
            if (len(rs) != cell["n"] or mean != cell["mean_accuracy"]
                    or {str(k): v for k, v in per_rep.items()} != cell["per_rep"]):
                bad.append((key, cell["n"], len(rs), cell["mean_accuracy"], mean))
        truth("replication summary recomputes from its raw rows (mean and per-rep)",
              not bad, bad[:3])
        spend_ok = round(sum(r["cost_usd"] for r in rows), 6) == meta["ACTUAL_SPEND_USD"]
        truth("replication spend equals the sum of metered per-row costs", spend_ok,
              (round(sum(r["cost_usd"] for r in rows), 6), meta["ACTUAL_SPEND_USD"]))
        stable = [k for k, c in summ["cells"].items()
                  if "llama" in k.split("|")[0] or "Qwen3.8" in k.split("|")[0]]
        flat = all(len(set(summ["cells"][k]["per_rep"].values())) == 1 for k in stable)
        truth("the deterministic arms reproduce their cell means in every repetition", flat,
              {k: summ["cells"][k]["per_rep"] for k in stable})
        print(f"  replication: {len(rows)} attempts, ${meta['ACTUAL_SPEND_USD']:.6f}, "
              f"{len(summ['cells'])} cells")

    probe = HERE / "exp1" / "results" / "raw" / "reps_glm-budget-8192.jsonl"
    if probe.exists():
        rows = [json.loads(l) for l in probe.read_text().splitlines() if l.strip()]
        base = [json.loads(l) for l in
                (HERE / "exp1" / "results" / "raw" / "reps_exp1-reps-v1.jsonl")
                .read_text().splitlines() if l.strip()]
        arm = "Together GLM-5.3-Flash"

        def mean(rs):
            xs = [r["quality"] for r in rs if r.get("quality") is not None]
            return round(statistics.fmean(xs), 4) if xs else None
        hi = mean([r for r in rows if r["prompt_class"] == "compute_via_code"])
        lo = mean([r for r in base if r["arm"] == arm and r["prompt_class"] == "compute_via_code"])
        truth("budget probe: the same arm rises from 0.22 at 2048 tokens to 1.00 at 8192",
              (lo, hi) == (0.2222, 1.0), (lo, hi))
        print(f"  budget probe: compute accuracy {lo} @2048 -> {hi} @8192")

    ann = HERE / "exp2" / "results" / "derived" / "annotation_agreement.json"
    if ann.exists():
        r = json.loads(ann.read_text())
        truth("annotation study covers 124 items", r["n_items"] == 124, r["n_items"])
        truth("rules vs author kappa on 124 items is substantial (0.9589 after ruleset v5)",
              r["rules_vs_author"]["kappa"] == 0.9589, r["rules_vs_author"]["kappa"])
        truth("the independent annotator agrees with the author more than the rules do",
              r["annotator_vs_author"]["kappa"] > r["rules_vs_author"]["kappa"],
              (r["annotator_vs_author"]["kappa"], r["rules_vs_author"]["kappa"]))
        truth("the rules do not degrade on the newly written 80 items",
              r["by_set"]["prompts_expansion.jsonl"]["rules_vs_author"] >= 0.90,
              r["by_set"]["prompts_expansion.jsonl"]["rules_vs_author"])
        # the same ruleset, classified through a different code path, must reproduce exp2-v5
        truth("rules-on-44 in the annotation study equals the exp2-v5 recorded accuracy",
              r["by_set"]["prompts.jsonl"]["rules_vs_author"]
              == all_metrics["exp2-v5"]["M3_accuracy"],
              (r["by_set"]["prompts.jsonl"]["rules_vs_author"],
               all_metrics["exp2-v5"]["M3_accuracy"]))
        print(f"  annotation: n={r['n_items']} kappa rules/author={r['rules_vs_author']['kappa']} "
              f"annotator/author={r['annotator_vs_author']['kappa']}")

    ra = HERE / "exp2" / "results" / "derived" / "rules_audit.json"
    if ra.exists():
        r = json.loads(ra.read_text())
        truth("NO under-classification remains on either privacy battery",
              r["all"]["under"] == [], r["all"]["under"])
        truth("the original 44 items still score 0.9318 under the revised ruleset, so the "
              "published exp2 numbers are unchanged",
              r["by_set"]["prompts.jsonl"]["accuracy"] == all_metrics["exp2-v5"]["M3_accuracy"],
              (r["by_set"]["prompts.jsonl"]["accuracy"],
               all_metrics["exp2-v5"]["M3_accuracy"]))
        truth("the expansion is perfect only because the ruleset was revised against it; the "
              "paper says so and the independent annotator is the check",
              r["by_set"]["prompts_expansion.jsonl"]["accuracy"] == 1.0,
              r["by_set"]["prompts_expansion.jsonl"]["accuracy"])
        print(f"  rules audit: policy {r['policy_label']} / rules {r['rules_label']}, "
              f"under={len(r['all']['under'])} over={len(r['all']['over'])}")
        d = r["dev_a"]
        truth("battery A (used to design the guard) has zero under-classifications after it",
              len(d["under"]) == 0 and d["accuracy"] == 0.6719, (d["accuracy"], d["under"]))
        h = r["heldout"]
        truth("battery B is fresh and reported separately from tuning and from A", h["n"] == 72, h["n"])
        truth("battery B accuracy is 0.5417 — phrase engineering does not generalise",
              h["accuracy"] == 0.5417, h["accuracy"])
        truth("battery B still exposes under-classifications (reported, not tuned away)",
              len(h["under"]) == 7, h["under"])
        truth("most of battery B matches no rule (coverage is the binding problem)",
              len(h["defaulted"]) >= 40, len(h["defaulted"]))

    ann2 = HERE / "exp2" / "results" / "derived" / "annotation_agreement_deepseek.json"
    if ann2.exists():
        r = json.loads(ann2.read_text())
        truth("a second annotator from a different family reproduces the author's labels "
              "(kappa > 0.95)", r["annotator_vs_author"]["kappa"] > 0.95,
              r["annotator_vs_author"]["kappa"])
        truth("rules agree with that annotator far less than it agrees with the author",
              r["rules_vs_annotator"]["kappa"] < r["annotator_vs_author"]["kappa"] - 0.15,
              (r["rules_vs_annotator"]["kappa"], r["annotator_vs_author"]["kappa"]))
        print(f"  second annotator: {r['annotator']} kappa={r['annotator_vs_author']['kappa']} "
              f"rules-vs-annotator={r['rules_vs_annotator']['kappa']}")

    bud = HERE / "exp1" / "results" / "raw" / "summary_budget-8192-all.json"
    if bud.exists():
        r = json.loads(bud.read_text())
        cells = r["cells"]
        cheap = cells.get("Together GLM-5.3-Flash|compute_via_code", {})
        local = [k for k in cells if k.startswith("llama")]
        truth("budget probe: the cheap arm reaches 1.00 at 8192 tokens", cheap.get("mean_accuracy") == 1.0,
              cheap.get("mean_accuracy"))
        truth("budget probe: local arms have no measurable cell at 8192 (window limit)",
              not any("compute" in k for k in local), local)
        print(f"  budget 8192: cheap={cheap.get('mean_accuracy')} local_cells={len(local)}")

    cit = HERE.parent / "05-model-router" / "paper" / "citations_verified.json"
    cit = HERE.parent / "paper" / "citations_verified.json"
    if cit.exists():
        r = json.loads(cit.read_text())
        truth("every DOI cited by the paper resolves and, where derivable, matches title+author",
              r["n_pass"] == r["n"], (r["n_pass"], r["n"]))
        print(f"  citations: {r['n_pass']}/{r['n']} verified")
    figs = HERE.parent / "paper" / "figures"
    if figs.exists():
        truth("both paper figures exist", all((figs / f).exists() for f in
              ("fig1_cost_accuracy.pdf", "fig2_sweep.pdf")),
              sorted(p.name for p in figs.glob("*.pdf")))


def main():
    store = Store(L5)

    # ---- global integrity ----
    for ds in ("models", "capabilities", "policy", "rules", "decision_log"):
        ok, detail = store.verify_chain(ds)
        truth(f"chain verifies: {ds}", ok, detail)

    act = store.records("policy", status="active")
    default = ([p for p in act if p.get("is_default") is True]
               or sorted(act, key=lambda p: p["created_at"]))[-1]
    truth("shipped default does not classify (classifier.mode = trust_user)",
          (default.get("classifier") or {}).get("mode") == "trust_user",
          default.get("classifier", {}).get("mode"))
    trust = [d for d in store.records("decision_log")
             if d.get("classifier_mode") == "trust_user"]
    truth("every trust_user decision records the mode and carries no privacy warning",
          all(d["privacy_class"] == "public" and not d.get("privacy_warning") for d in trust),
          len(trust))
    print(f"  default policy {default['version_label']}: mode=trust_user; "
          f"{len(trust)} decisions recorded under it")

    battery, man = load_battery(HERE / "exp2")
    all_metrics = {}
    for run, label in (("exp2", "v1 policy, substring matcher, ruleset v1"),
                       ("exp2-v2", "v2 policy, substring matcher, ruleset v1"),
                       ("exp2-v2b", "v2 policy, word-boundary matcher, ruleset v1"),
                       ("exp2-v3", "v3-conf-cloud ARM: confidential may reach the cloud"),
                       ("exp2-v4", "v5-lineage policy v4: ruleset v2 + word boundaries"),
                       ("exp2-v5", "policy v5: ruleset v3 + word boundaries (current default)")):
        all_metrics[run] = exp2_metrics(store, battery, man, run, label)

    # ---- A.4 falsifiers from the pre-registered amendment ----
    v1, v2 = all_metrics["exp2"], all_metrics["exp2-v2"]
    v2b = all_metrics["exp2-v2b"]
    truth("A.4 v2 stores no prompt text at all", v2["decisions_with_text_stored"] == 0,
          v2["text_stored_ids"][:3])
    truth("A.4 v2 has no scoped leak (the fix works going forward)",
          not v2["M7_leaks"].get("labelled_private_scoped"),
          v2["M7_leaks"].get("labelled_private_scoped"))
    truth("A.4 classifier metrics are IDENTICAL under v1 and v2 (a logging change must not "
          "change classification)",
          (v1["M3_confusion"], v1["M4_ids"], v1["M5_ids"], v1["M6_defaulted_ids"]) ==
          (v2["M3_confusion"], v2["M4_ids"], v2["M5_ids"], v2["M6_defaulted_ids"]))

    # ---- the word-boundary matcher fix: what exactly did it change? ----
    truth("v2b stores no prompt text", v2b["decisions_with_text_stored"] == 0)
    truth("v2b has no scoped leak", not v2b["M7_leaks"].get("labelled_private_scoped"))
    diff = sorted(b for b in v2["classifications"]
                  if v2["classifications"][b] != v2b["classifications"][b])
    truth("the matcher fix changes exactly one classification on this battery: u10, the "
          "pandas/nda substring case (confidential -> private default)",
          diff == ["u10"] and v2["classifications"]["u10"] == "confidential"
          and v2b["classifications"]["u10"] == "private", diff)
    truth("the matcher fix does not change accuracy on this battery (u10 was over-classified "
          "either way) — reported so the fix is not credited with a gain it did not produce",
          v2b["M3_accuracy"] == v2["M3_accuracy"], (v2["M3_accuracy"], v2b["M3_accuracy"]))

    # ---- the experimental arm: does gate compliance still equal real privacy? ----
    v3 = all_metrics["exp2-v3"]
    truth("v3 arm: M1 gate compliance is still 0 violations (the gate obeys its own verdict)",
          v3["M1_gate_violations"] == 0, v3["M1_violating_ids"])
    print(f"\n  v3 arm M2 end-to-end violations: {v3['M2_end_to_end_violations']}"
          f"/{v3['M2_labelled_private']} -> {v3['M2_violating_ids']}")
    if v3["M2_end_to_end_violations"] > v2b["M2_end_to_end_violations"]:
        print("  DEMONSTRATED, not argued: letting a user map `confidential` to the cloud "
              "turns one classifier under-classification into real data leaving the device, "
              "while M1 still reads 0 violations. This is why spec §10.1 reports classifier "
              "error separately from gate compliance, and why the privacy->locus map is a "
              "safety-critical parameter rather than a convenience setting.")
    else:
        print("  no divergence observed on this battery: the under-classified prompt did not "
              "reach a cloud locus even with confidential->cloud allowed")

    # ---- the classifier improved ONLY through data + one matcher fix, never by relaxing
    #      a safety rule: every remaining error is in the safe (over-protective) direction ----
    v4m, v5m = all_metrics["exp2-v4"], all_metrics["exp2-v5"]
    truth("ruleset progression raises accuracy monotonically (v1 0.8182 -> v2 0.8864 -> v3 0.9318)",
          v2b["M3_accuracy"] < v4m["M3_accuracy"] < v5m["M3_accuracy"],
          (v2b["M3_accuracy"], v4m["M3_accuracy"], v5m["M3_accuracy"]))
    truth("under-classification is 0 from ruleset v2 onward",
          v4m["M5_under_classified"] == 0 and v5m["M5_under_classified"] == 0,
          (v4m["M5_ids"], v5m["M5_ids"]))
    truth("over-classification falls 7 -> 5 -> 3",
          [v2b["M4_over_classified"], v4m["M4_over_classified"], v5m["M4_over_classified"]] ==
          [7, 5, 3])
    truth("every remaining error under the current default is OVER-classification (safe direction)",
          set(v5m["M4_ids"] + v5m["M5_ids"]) == set(v5m["M4_ids"]),
          {"over": v5m["M4_ids"], "under": v5m["M5_ids"]})
    truth("no prompt falls through to the fail-closed default under ruleset v3",
          v5m["M6_defaulted_ids"] == [], v5m["M6_defaulted_ids"])
    truth("M1 and M2 stay at 0 violations in EVERY run (no accuracy gain was bought with privacy)",
          all(m["M1_gate_violations"] == 0 and m["M2_end_to_end_violations"] == 0
              for m in all_metrics.values()),
          {r: (m["M1_gate_violations"], m["M2_end_to_end_violations"])
           for r, m in all_metrics.items()})
    truth("text is stored for 0 decisions in every run from policy v2 onward",
          all(m["decisions_with_text_stored"] == 0
              for r, m in all_metrics.items() if r != "exp2"),
          {r: m["decisions_with_text_stored"] for r, m in all_metrics.items()})

    # The standing log still carries the v1 run's leak. That is a permanent result, not a
    # spine failure: append-only provenance cannot retract it (spec §10.11).
    print(f"\n  PERMANENT DEFECT (reported, not a spine failure): the standing log still "
          f"contains {sorted(v2b['M7_leaks'].get('labelled_private_standing', {}))} written by "
          f"the v1 run, and {sorted(k for k in v2b['M7_leaks'].get('classified_private_standing', {}))}"
          f" classified-private prompts stored under the v1 logging rule")

    # ---- the replay audit, for every run ----
    for run in ("exp2", "exp2-v2", "exp2-v2b", "exp2-v3", "exp2-v4", "exp2-v5"):
        needles = [b["text"] for b in battery if b["label"] == "private"]
        labels = [b["id"] for b in battery if b["label"] == "private"]
        code, rep = audit(store=store, run=run, needles=needles, needle_labels=labels,
                          out=str(HERE / "exp2" / "results" / "raw" / f"audit_{run}.json"))
        all_metrics[run]["audit_exit"] = code
        all_metrics[run]["audit_failures"] = rep["failures"]
        all_metrics[run]["audit_coverage"] = rep["coverage"]
        print(f"  audit(run={run}) exit={code} failures={rep['failures']}")

    e13 = exp13_metrics(store)
    e3s = exp3_sweep_metrics(store, "exp3-sweep-v3")
    derived_artifacts(all_metrics)

    out = HERE / "exp2" / "results" / "derived"
    out.mkdir(parents=True, exist_ok=True)
    doc = {"experiment": "exp2 privacy policy compliance",
           "cost_usd": 0.0, "mode": "dry_run for every decision",
           "battery": {"sha256": man["sha256"], "n": man["n_prompts"],
                       "by_label": man["by_label"], "adversarial": man["adversarial"]},
           "runs": all_metrics, "exp1_exp3": e13, "exp3_sweep": e3s}
    (out / "headline_numbers.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"\nheadline numbers -> {(out / 'headline_numbers.json').relative_to(L5)}")
    if e13:
        o2 = HERE / "exp1" / "results" / "derived"
        o2.mkdir(parents=True, exist_ok=True)
        (o2 / "headline_numbers.json").write_text(
            json.dumps({"experiment": "exp1 frontier + exp3 verifiability",
                        "cost_usd": e13.get("actual_spend_usd"), **e13},
                       indent=1, sort_keys=True) + "\n")
        print(f"headline numbers -> {(o2 / 'headline_numbers.json').relative_to(L5)}")
    o3 = HERE / "exp3" / "results" / "derived"
    o3.mkdir(parents=True, exist_ok=True)
    (o3 / "headline_numbers.json").write_text(
        json.dumps({"experiment": "exp3 verifiability: measured eligibility and its price",
                    "cost_usd": 0.0, "sweep": e3s,
                    "capability_rows_from_exp1": e13.get("capabilities")},
                   indent=1, sort_keys=True) + "\n")
    print(f"headline numbers -> {(o3 / 'headline_numbers.json').relative_to(L5)}")

    # ---- persist the verification summary (the abstract's headline audit numbers) ----
    # The claim map (paper/claim_map) points at this file; without it the numbers
    # "N checks pass on M decisions across eight runs" would exist only in the paper.
    audited_runs = ["exp2", "exp2-v2", "exp2-v2b", "exp2-v3", "exp2-v4", "exp2-v5",
                    "exp1-pilot-v3", "exp3-sweep-v3"]
    decisions_replayed = {}
    for run in audited_runs:
        if run.startswith("exp2"):
            arep = json.loads((HERE / "exp2" / "results" / "raw" / f"audit_{run}.json").read_text())
        elif run.startswith("exp1"):
            arep = json.loads((HERE / "exp1" / "results" / "raw" / f"audit_{run}.json").read_text())
        else:
            arep = json.loads((HERE / "exp3" / "results" / "raw" / f"audit_{run}.json").read_text())
        decisions_replayed[run] = arep["coverage"]["decisions"]
    summary = {
        "artifact": "verification summary (frozen by spine.py at the end of every run)",
        "spine_checks_passed": CHECKS["pass"],
        "spine_checks_failed": CHECKS["fail"],
        "spine_exit": 0 if not fails else 1,
        "runs_audited": audited_runs,
        "n_runs": len(audited_runs),
        "decisions_replayed_per_run": decisions_replayed,
        "decisions_replayed_total": sum(decisions_replayed.values()),
    }
    # audit check battery size per run (exp2 runs carry the §6.2 privacy scans; the two
    # non-privacy runs do not), and the composite headline count
    audit_checks = {}
    for run in audited_runs:
        exp = "exp2" if run.startswith("exp2") else ("exp1" if run.startswith("exp1") else "exp3")
        arep = json.loads((HERE / exp / "results" / "raw" / f"audit_{run}.json").read_text())
        audit_checks[run] = len(arep["checks"])
    summary["audit_checks_per_run"] = audit_checks
    summary["audit_checks_total"] = sum(audit_checks.values())
    summary["verification_checks_total"] = CHECKS["pass"] + sum(audit_checks.values())
    summary_dir = HERE / "results" / "derived"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "verification_summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"verification summary -> {(summary_dir / 'verification_summary.json').relative_to(L5)}"
          f" ({CHECKS['pass']} checks, {sum(decisions_replayed.values())} decisions, "
          f"{len(audited_runs)} runs)")

    print("\n" + "=" * 60)
    if fails:
        print(f"SPINE FAILED: {len(fails)} numbers do not recompute -> {fails}")
        sys.exit(1)
    print("SPINE OK — every exp2 headline number recomputed from frozen artifacts, "
          "and raw agrees with the log")


if __name__ == "__main__":
    main()
