#!/usr/bin/env python3
"""Router tests: gates, refusals, privacy of the log, determinism, and the claim that
ablation is a DATA edit. Runs against a throwaway copy of the real frozen registry, so
the live datasets are untouched. Exit 0 = all pass.

Run with the venv: .venv/bin/python router/test_router.py
"""
import copy, json, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from decide import Router, aggregate_privacy_binding, prompt_hmac  # noqa: E402
from store import DATASETS, Store, new_id, now  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        fails.append(label)


def temp_store():
    """Real schemas, real registry/policy/rules — but an EMPTY decision log, so the suite
    measures only what it creates and never inherits a frozen experiment's records."""
    tmp = Path(tempfile.mkdtemp())
    for ds in DATASETS:
        src, dst = ROOT / "datasets" / ds, tmp / "datasets" / ds
        dst.mkdir(parents=True)
        shutil.copy(src / "schema.json", dst / "schema.json")
        shutil.copy(src / "SUPERSEDED.json", dst / "SUPERSEDED.json")
        if ds == "decision_log":
            for f in ("data.jsonl", "bindings.jsonl"):
                (dst / f).touch()
        else:
            for f in ("data.jsonl", "bindings.jsonl"):
                shutil.copy(src / f, dst / f)
        (dst / "history.jsonl").touch()      # fresh chain per test run
    return Store(tmp), tmp


def req(text, **kw):
    return {"request_id": new_id("req"), "text": text, "attachments": [], **kw}


s, tmp = temp_store()
r = Router(store=s, free_ram_gb=12.0)
pol = r.policy()
print(f"registry: {len(s.records('models', status='active'))} active variants; "
      f"policy {pol['version_label']}; snapshot {s.registry_snapshot_hash()[:12]}…\n")

BIG = ("source,amount,vendor,date\n" + "".join(
    f"src_{i},{i*3.11:.2f},Vendor {i},2026-0{i%9+1}-1{i%9}\n" for i in range(1400)))

# ---- 1. public request: with both cost knobs at 0, local wins (spec §3.2 collapse) ----
d = r.route(req("how do i write a python function to sort a list? example code"), dry_run=True, run="t")
check("public -> planned", d["outcome"] == "planned", d["outcome"])
check("public -> local wins while shadow+latency prices are 0 (documented collapse)",
      d["chosen"]["locus"] == "local", d["chosen"])

# ---- 2. private request: every cloud variant must be excluded by gate 1 ----
d2 = r.route(req("Summarise my medical diagnosis and the prescription my doctor gave me"),
             dry_run=True, run="t")
rec2 = s.get("decision_log", d2["decision_ref"])
check("private classified private", rec2["privacy_class"] == "private", rec2["privacy_class"])
check("private -> local", d2["chosen"]["locus"] == "local", d2["chosen"])
g1 = rec2["gate_trace"][0]
check("gate 1 excluded every cloud variant",
      g1["survivors"] and all(s.get("models", m)["locus"] == "local" for m in g1["survivors"]),
      g1["survivors"])
check("gate 1 exclusion reasons name the locus rule",
      all("not_allowed_for_private" in e["reason"] for e in g1["excluded"]), g1["excluded"][:1])
check("private prompt text NOT stored", rec2["prompt_text"] is None)
check("private prompt HMAC stored", len(rec2["prompt_hmac"]) == 64)
check("HMAC is stable for identical text", prompt_hmac(rec2["prompt_text"] or
      "Summarise my medical diagnosis and the prescription my doctor gave me") == rec2["prompt_hmac"])

# ---- 3. private + too long for the local window: refusal, not a silent cloud fallback ----
d3 = r.route(req("Summarise my medical records and my bank account statements in this table:\n" + BIG),
             dry_run=True, run="t")
rec3 = s.get("decision_log", d3["decision_ref"])
check("private + oversized -> refused", d3["outcome"] == "refused", d3["outcome"])
check("refusal reason names gate 3", d3["refusal_reason"] in
      ("context_exceeds_all", "local_resource_unavailable"), d3["refusal_reason"])
check("refused decision logged no chosen model", rec3["chosen"] is None)
check("gate trace shows the gate that emptied the set",
      rec3["gate_trace"][-1]["survivors"] == [], rec3["gate_trace"][-1]["gate"])
check("truncated trace stops at the emptying gate",
      len(rec3["gate_trace"]) <= len(pol["gates"]["order"]))

# ---- 3b. FAIL CLOSED: an ordinary business prompt with no marker is treated as private ----
d3b = r.route(req("Summarise the totals in this table and list the top vendors"), dry_run=True, run="t")
rec3b = s.get("decision_log", d3b["decision_ref"])
check("unmarked prompt fails closed to private (spec §2.1)", rec3b["privacy_class"] == "private",
      rec3b["privacy_class"])
check("fail-closed is visible: no rule matched, so matched_rank is null",
      rec3b["classifier_matched_rank"] is None, rec3b["classifier_matched_rank"])
check("fail-closed routes to the local arm", d3b["chosen"]["locus"] == "local")

# ---- 4. public + oversized: cloud survives, most expensive model killed by its 4k window ----
PUB = "Write example code in python to summarise the totals in this table:\n" + BIG
d4 = r.route(req(PUB, attachments=[{"type": "csv", "sha256": "x", "bytes": len(BIG)}]),
             dry_run=True, run="t")
rec4 = s.get("decision_log", d4["decision_ref"])
check("public marker recognised -> public", rec4["privacy_class"] == "public", rec4["privacy_class"])
check("public + csv -> tabular_summary", rec4["task_class"] == "tabular_summary", rec4["task_class"])
check("public + oversized -> cloud chosen", d4["chosen"]["locus"] == "cloud", d4["chosen"])
g3 = [g for g in rec4["gate_trace"] if g["gate"] == "context_fit"][0]
local_excl = [e for e in g3["excluded"]
              if s.get("models", e["model_ref"])["locus"] == "local"]
check("gate 3 excluded the local arm on its 8192-token runtime window, not on price",
      local_excl and "context_exceeds" in local_excl[0]["reason"], local_excl[:1])
check("gate 3 kept the large-context cloud variants",
      len(g3["survivors"]) >= 2, g3["survivors"])
check("cheapest feasible variant chosen (argmin over the feasible set)",
      d4["chosen"]["model_ref"] == rec4["feasible_set"][0]["model_ref"])
check("feasible set is sorted by (cost, id)",
      [(f["expected_cost_usd"], f["model_ref"]) for f in rec4["feasible_set"]] ==
      sorted((f["expected_cost_usd"], f["model_ref"]) for f in rec4["feasible_set"]))

# ---- 5. determinism: same frozen inputs -> same decision content ----
txt = PUB
a = r.route(req(txt, attachments=[{"type": "csv", "sha256": "x", "bytes": len(BIG)}]), dry_run=True, run="t")
b = r.route(req(txt, attachments=[{"type": "csv", "sha256": "x", "bytes": len(BIG)}]), dry_run=True, run="t")
ra, rb = s.get("decision_log", a["decision_ref"]), s.get("decision_log", b["decision_ref"])
strip = lambda rec: {k: v for k, v in rec.items() if k not in ("id", "created_at", "request_ref", "binding_ref")}
check("decisions are byte-identical modulo id/timestamps", strip(ra) == strip(rb))

# ---- 6. ABLATION IS A DATA EDIT: raise the latency price, watch the choice flip ----
pol2 = copy.deepcopy(pol)
pol2["id"] = new_id("pol")
pol2["version_label"] = "v2-latency"
pol2["created_at"] = now()
pol2["objective"]["latency_price_usd_per_s"] = 0.05
pol2["source_ref"] = [pol["id"]]
s.append("policy", pol2)
r2 = Router(store=s, free_ram_gb=12.0, policy_label="v2-latency")
c = r2.route(req(txt, attachments=[{"type": "csv", "sha256": "x", "bytes": len(BIG)}]), dry_run=True, run="t2")
check("policy v2 is active and selected", r2.policy()["id"] == pol2["id"])
check("pricing local waiting time flips the choice to cloud",
      c["chosen"]["locus"] == "cloud", c["chosen"])
check("the flip changed no code and no capability row",
      s.records("capabilities", status="active") == [])

# ---- 7. privacy compliance over the whole log, as a bound aggregate claim ----
bid, stats = aggregate_privacy_binding(s, "t", pol["id"])
check("aggregate claim: 0 private requests routed to cloud",
      stats["violations"] == 0 and stats["private_requests"] >= 2, stats)
check("aggregate binding records BOTH sides",
      bid["assertion"]["both_sides"] == {"private_requests": stats["private_requests"],
                                         "private_routed_to_cloud": 0})

# ---- 8. bindings resolve; every decision has exactly one ----
bl = s.bindings("decision_log")
decs = s.records("decision_log")
check("one binding per decision (+1 aggregate)", len(bl) == len(decs) + 1, f"{len(bl)} vs {len(decs)}")
check("every decision points at an existing binding",
      all(any(x["id"] == d["binding_ref"] for x in bl) for d in decs))
check("every binding has non-empty resolving sources",
      all(x["sources"] and all(src.get("dataset") for src in x["sources"]) for x in bl))

# ---- 8b. the 1e6 unit trap is pinned (spec §3.1) — it already cost a mistake once ----
import cost as K  # noqa: E402
ok_model = {"id": "mdl_x", "locus": "cloud",
            "price": {"input_per_mtok": 0.30, "output_per_mtok": 0.30,
                      "unit": "usd_per_million_tokens"}}
check("1M tokens at $0.30/Mtok costs $0.30, not $300,000",
      abs(K.expected_cost_usd(ok_model, 1_000_000, 0, pol) - 0.30) < 1e-9,
      K.expected_cost_usd(ok_model, 1_000_000, 0, pol))
for bad in ({"input_per_mtok": 300000.0, "output_per_mtok": 0.3, "unit": "usd_per_million_tokens"},
            {"input_per_mtok": 0.3, "output_per_mtok": 0.3, "unit": "usd_per_token"}):
    try:
        K.expected_cost_usd({"id": "mdl_bad", "locus": "cloud", "price": bad}, 1000, 100, pol)
        check(f"unit guard rejects {bad.get('unit')} {bad['input_per_mtok']}", False)
    except K.CostError as e:
        check(f"unit guard rejects {bad.get('unit')}/{bad['input_per_mtok']}", True, str(e)[:44])

# ---- 9. chains verify; tampering is caught ----
for ds in DATASETS:
    ok, detail = s.verify_chain(ds)
    check(f"chain verifies: {ds}", ok, detail)
hp = tmp / "datasets/decision_log/history.jsonl"
lines = hp.read_text().splitlines()
# the meaningful tamper: rewrite a PRIVATE decision's class to hide that it was private.
# (Flipping a record that is already "public" would be a no-op and prove nothing.)
idx = next(i for i, l in enumerate(lines)
           if (json.loads(l).get("record") or {}).get("privacy_class") == "private")
e = json.loads(lines[idx])
before = e["record"]["privacy_class"]
e["record"]["privacy_class"] = "public"
lines[idx] = json.dumps(e, sort_keys=True, separators=(",", ":"))
hp.write_text("\n".join(lines) + "\n")
ok, detail = s.verify_chain("decision_log")
check(f"tampered private->public detected (was {before!r}; {detail[:44]})", not ok)

shutil.rmtree(tmp)
print("\n" + "=" * 54)
if fails:
    print(f"ROUTER TESTS FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ROUTER TESTS OK")
