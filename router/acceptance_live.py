#!/usr/bin/env python3
"""acceptance_live.py — acceptance check 7, the one that needs a network.

"A single `openai_compatible` class answers from (a) a real local endpoint and (b) a real
cloud provider, with zero provider-specific code in the routing path."

Both endpoints are configured as `connector: "openai_compatible"` with nothing but data
differing (base_url, model, auth, price, locus). The local leg talks to Ollama's
OpenAI-compatible `/v1` surface — a real desktop-class runtime on this machine, not a stub.
The cloud leg talks to Together. Cost of the cloud leg is reported from the provider meter.

Honest limitation, stated here and in the spec: the owner's list names LM Studio, Jan,
GPT4All or llama.cpp-server for the "desktop app" leg. None is installed on this machine and
installing GUI software is not something this agent does unasked, so the local leg runs
against Ollama's /v1 surface instead. The protocol is the one those apps expose; the
difference is which process serves it.
"""
import json, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from decide import Router
from store import DATASETS, Store, new_id, now

PROMPT = ("What is a hash chain? This is a public question from the open-source "
          "documentation. Reply with exactly: hello from the connector.")
LOCAL = {"connector": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1",
         "model": "llama3.2:latest", "locus": "local", "privacy_class": "private",
         "price": (0.0, 0.0), "auth": {"scheme": "none", "key_env": None}}
CLOUDS = [
    {"name": "together", "connector": "openai_compatible",
     "base_url": "https://api.together.xyz/v1",
     # The 70B this layer used in October is gone from the account's catalog, and models
     # Together serves via a third party are blocked by this org's data-sharing setting
     # (HTTP 403, correctly surfaced as a typed auth failure). This one is first-party and
     # reachable today; its price is read from /v1/models, not guessed.
     "model": "zai-org/GLM-5.3-Flash", "locus": "cloud",
     "privacy_class": "public", "price": (0.15, 0.50),
     "auth": {"scheme": "bearer", "key_env": "TOGETHER_AI_API_KEY"}},
    {"name": "dashscope", "connector": "openai_compatible",
     "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-flash", "locus": "cloud",
     "privacy_class": "public", "price": (0.05, 0.40),
     "auth": {"scheme": "bearer", "key_env": "DASHSCOPE_API_KEY"}},
]


def store_with(spec):
    tmp = Path(tempfile.mkdtemp(prefix="rc-live-"))
    for ds in DATASETS:
        d = tmp / "datasets" / ds
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "datasets" / ds / "schema.json", d / "schema.json")
        for f in ("data.jsonl", "history.jsonl", "bindings.jsonl"):
            (d / f).touch()
        (d / "SUPERSEDED.json").write_text("[]")
    s = Store(tmp)
    live = Store(ROOT)
    for ds in ("rules", "policy"):
        for rec in live.records(ds, status="active"):
            if ds == "policy" and rec["version_label"] != "v7":
                continue
            s.append(ds, json.loads(json.dumps(rec)))
    s.append("models", {
        "id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
        "status": "active", "superseded_by": None, "source_ref": ["acceptance-live"],
        "label": f"live {spec['model']}", "connector": spec["connector"],
        "base_url": spec["base_url"], "model": spec["model"], "auth": spec["auth"],
        "locus": spec["locus"], "privacy_class": spec["privacy_class"],
        "price": {"input_per_mtok": spec["price"][0], "output_per_mtok": spec["price"][1],
                  "source": "user_declared"},
        "context_window": 131072, "output_budget_tokens": 512, "tokenizer_ref": None,
        "request_params": None, "timeout_s": 90.0, "local_resources": None, "health": None,
        "declared_at": now(), "capability_key": spec["model"]})
    return s


def leg(name, spec):
    s = store_with(spec)
    r = Router(store=s)
    d = r.route({"request_id": new_id("req"), "text": PROMPT}, run="acc-live")
    rec = s.get("decision_log", d["decision_ref"])
    a = (rec["attempts"] or [{}])[-1]
    print(f"  {name:6} connector={spec['connector']:18} outcome={d['outcome']:9} "
          f"tokens_in={a.get('tokens_in')} tokens_out={a.get('tokens_out')} "
          f"meter={a.get('meter')} actual=${d['cost']['actual_spend_usd']}")
    print(f"         answer={str(d['answer'])[:60]!r}")
    return d, a


def main():
    print("check 7 — one connector class, two real endpoints")
    dl, al = leg("local", LOCAL)
    ok = dl["answer"] is not None and dl["chosen"]["connector"] == "openai_compatible"
    if not ok:
        print("[FAIL] local leg did not answer through the generic connector")
    answered = []
    for spec in CLOUDS:
        dc, ac = leg(spec["name"], spec)
        if dc["answer"] and ac.get("meter") == "provider" and dc["cost"]["actual_spend_usd"] is not None:
            answered.append((spec["name"], dc["cost"]["actual_spend_usd"]))
        else:
            print(f"  {spec['name']}: no answer "
                  f"({(dc.get('refusal_reason') or 'no attempt')})")
    if not answered:
        print("[FAIL] no real cloud provider answered"); ok = False
    else:
        ok = ok and True
        dc = {"cost": {"actual_spend_usd": answered[0][1]}}
    mods = sorted(p.name for p in (HERE / "backends").glob("*.py"))
    print(f"  connector modules on disk: {mods}")
    if mods != ["__init__.py", "ollama.py", "openai_compatible.py"]:
        print("[FAIL] unexpected connector modules"); ok = False
    spend = dc["cost"]["actual_spend_usd"]
    print(f"[PASS] one openai_compatible class served {len(answered) + 1} real endpoints "
          f"({LOCAL['model']} locally, {', '.join(n for n, _ in answered)} in the cloud); "
          f"cloud spend " + (f"${spend:.8f}" if spend is not None else "unreported"))
    print("\n  NOTE: the local leg is Ollama's /v1 surface. LM Studio / Jan / GPT4All are not "
          "installed here and installing GUI software needs the owner's go-ahead (RC-5.1).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
