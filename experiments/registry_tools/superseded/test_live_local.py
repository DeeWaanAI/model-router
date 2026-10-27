#!/usr/bin/env python3
"""Live end-to-end test: real local inference + a STUB cloud backend.

Cost safety, two layers deep:
  1. The cloud backend here is a stub. It makes no network call and spends nothing.
  2. No TogetherBackend is constructed anywhere in this file.
So a cascade can be exercised end to end for $0. Stub token counts are NOT measurements
and are labelled run="stub-*"; nothing from them may enter a paper number.

The frozen datasets are untouched (throwaway store).

Run with the venv: .venv/bin/python router/test_live_local.py
"""
import json, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.ollama_backend import OllamaBackend  # noqa: E402
from decide import Router  # noqa: E402
from store import DATASETS, Store, new_id  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        fails.append(label)


class StubCloud:
    """Canned cloud backend. Always returns valid JSON so a cascade terminates at depth 2."""
    name = "together"

    def __init__(self):
        self.calls = []

    def complete(self, model, prompt, expected_out=None):
        self.calls.append(model["upstream_id"])
        return {"text": '{"total": 200.5, "source": "stub"}', "tokens_in": 40, "tokens_out": 12,
                "finish_reason": "stop", "wall_clock_s": 0.0, "confidence": None,
                "backend": self.name, "http_status": 0, "raw": {"stub": True}}


def temp_store():
    """Real schemas and registry, EMPTY decision log — the suite measures only its own."""
    tmp = Path(tempfile.mkdtemp())
    for ds in DATASETS:
        src, dst = ROOT / "datasets" / ds, tmp / "datasets" / ds
        dst.mkdir(parents=True)
        shutil.copy(src / "schema.json", dst / "schema.json")
        shutil.copy(src / "SUPERSEDED.json", dst / "SUPERSEDED.json")
        if ds != "decision_log":
            for f in ("data.jsonl", "bindings.jsonl"):
                shutil.copy(src / f, dst / f)
        else:
            for f in ("data.jsonl", "bindings.jsonl"):
                (dst / f).touch()
        (dst / "history.jsonl").touch()
    return Store(tmp), tmp


s, tmp = temp_store()
stub = StubCloud()
# The stub is registered under EVERY cloud backend name, so whichever variant wins the
# argmin, the cascade still costs nothing. Which cloud arm is cheapest is registry data
# and changes as the registry changes; this test must not depend on it.
r = Router(store=s, backends={"ollama": OllamaBackend(), "together": stub, "dashscope": stub},
           free_ram_gb=12.0)

# ---- 1. public + structured output: real local inference, metered tokens, $0 ----
pub = ('python exercise. Reply with ONLY valid JSON: {"total": <number>} '
       "for the amounts 120.50 and 80.00")
d = r.route({"request_id": new_id("req"), "text": pub, "attachments": []}, run="live-local")
rec = s.get("decision_log", d["decision_ref"])
a1 = rec["attempts"][0]
check("first attempt is the cheapest feasible variant (local, $0)",
      s.get("models", a1["model_ref"])["locus"] == "local", a1["model_ref"])
check("provider-metered input tokens > 0", a1["tokens_in"] > 0, a1["tokens_in"])
check("provider-metered output tokens > 0", a1["tokens_out"] > 0, a1["tokens_out"])
check("local actual cost is exactly $0", a1["actual_cost_usd"] == 0.0, a1["actual_cost_usd"])
check("local attempt terminated normally", a1["finish_reason"] == "stop", a1["finish_reason"])
check("outcome recorded", d["outcome"] in ("answered", "escalated"), d["outcome"])
pol = r.policy()
stores_public_text = pol["logging"]["store_prompt_content"] == "never_for_private"
check(f"prompt text storage matches the active policy ({pol['version_label']}: "
      f"{pol['logging']['store_prompt_content']})",
      (rec["prompt_text"] == pub) if stores_public_text else (rec["prompt_text"] is None),
      rec["prompt_text"] is not None)
check("task classified structured_extract", rec["task_class"] == "structured_extract", rec["task_class"])

local_json_ok = a1["structural_check"]
print(f"    note: local 3B produced bare valid JSON: {local_json_ok}"
      f" (attempts={len(rec['attempts'])}, cloud stub calls={stub.calls})")
if local_json_ok:
    check("answered in one attempt, no escalation", d["outcome"] == "answered" and len(rec["attempts"]) == 1)
else:
    # A real finding, not a test failure: the small local arm broke the output discipline,
    # and the machine-checkable trigger caught it. This is the hazard experiment 3 measures.
    check("structural failure triggered escalation", d["outcome"] == "escalated", d["outcome"])
    check("escalation reason is the objective trigger, not self-reported confidence",
          rec["attempts"][1]["triggered_by"] == "structural_check_failed",
          rec["attempts"][1]["triggered_by"])
    check("escalation moved to the next-cheapest feasible variant, not a repeat",
          rec["attempts"][1]["model_ref"] == rec["feasible_set"][1]["model_ref"]
          and rec["attempts"][1]["model_ref"] != rec["attempts"][0]["model_ref"],
          [s.get("models", a["model_ref"])["name"] for a in rec["attempts"]])
    # With two $0 local arms the cascade spends nothing until the free capacity is
    # exhausted — a property worth pinning, and one the registry can change (spec §2.5).
    print(f"    note: escalation path = "
          f"{[s.get('models', a['model_ref'])['name'] for a in rec['attempts']]}, "
          f"cloud stub calls = {stub.calls}")
    check("cascade stopped at the policy depth",
          len(rec["attempts"]) <= r.policy()["objective"]["escalation"]["max_cascade_depth"],
          len(rec["attempts"]))

# ---- 2. private request, EXECUTED: the answer returns, the log keeps nothing ----
stub.calls.clear()
priv = "Summarise my medical prescription: aspirin 100mg daily and metformin 500mg twice daily."
d2 = r.route({"request_id": new_id("req"), "text": priv, "attachments": []}, run="live-local")
rec2 = s.get("decision_log", d2["decision_ref"])
check("private request stayed on the local arm",
      d2["chosen"]["locus"] == "local" and not stub.calls, d2["chosen"])
check("private request was answered", d2["outcome"] in ("answered", "escalated"), d2["outcome"])
check("the user still gets the answer", bool(d2["answer"]), (d2["answer"] or "")[:50])
check("private prompt text NOT in the record", rec2["prompt_text"] is None)
check("private response text NOT in the record", rec2["response_text"] is None)
check("private HMAC present (64 hex chars)", len(rec2["prompt_hmac"]) == 64)

# ---- 3. proof-of-absence scan over the whole log (spec §6.2, in miniature) ----
blobs = {"data.jsonl": (tmp / "datasets/decision_log/data.jsonl").read_text(),
         "history.jsonl": (tmp / "datasets/decision_log/history.jsonl").read_text(),
         "bindings.jsonl": (tmp / "datasets/decision_log/bindings.jsonl").read_text()}
needles = {"full private prompt": priv, "drug name": "metformin", "dose": "aspirin 100mg"}
hits = {n: {f: (v in txt) for f, txt in blobs.items()} for n, v in needles.items()}
check("no private payload anywhere in the decision log",
      not any(v for h in hits.values() for v in h.values()), json.dumps(hits))
# Non-vacuity control: the scan must be able to FIND things. The run label is in every
# record, so it must be found; and when the policy stores public text, the prompt must be
# too. Without this control a passing absence scan could simply mean the scan is broken.
check("scan is not vacuous: the run label IS found in the log",
      "live-local" in blobs["data.jsonl"])
if stores_public_text:
    check("scan is not vacuous: the stored public prompt IS found",
          pub[:30] in blobs["data.jsonl"])
else:
    check("policy stores no text, so the public prompt is absent too (scan still non-vacuous "
          "via the run label)", pub[:30] not in blobs["data.jsonl"])
check("all three log files were scanned", len(blobs) == 3 and all(blobs.values()))

# ---- 4. integrity after real execution ----
for ds in DATASETS:
    ok, detail = s.verify_chain(ds)
    check(f"chain verifies after live run: {ds}", ok, detail)
check("one binding per decision", len(s.bindings("decision_log")) == len(s.records("decision_log")))
check("every logged decision pins the policy and registry snapshot",
      all(x["policy_ref"] and len(x["registry_snapshot_hash"]) == 64 for x in s.records("decision_log")))

shutil.rmtree(tmp)
print("\n" + "=" * 54)
if fails:
    print(f"LIVE TESTS FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("LIVE TESTS OK — real local inference, provider-metered, $0 spend")
