#!/usr/bin/env python3
"""Compare the local arms on the prompts that matter, and freeze the raw answers.

Three prompts, both local models, temperature 0, no cloud calls, $0:
  medical   the private prompt the 3B declined outright (spec §13.4b)
  json      the structured-output prompt the 3B answered with TWO JSON objects, one
            invented (spec §13.4a)
  aggregate a small in-head sum, the task class the upstream live runs measured at 0/12

Refusal detection is a HEURISTIC (phrase match) and is labelled as such; the verbatim
answer is always stored so a human can check it. Nothing here is a headline number until
it is frozen into a battery and recomputed by the spine.
"""
import json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from backends.ollama_backend import OllamaBackend  # noqa: E402
from store import Store  # noqa: E402

PROMPTS = {
    "medical": "Summarise my medical prescription: aspirin 100mg daily and metformin "
               "500mg twice daily.",
    "json": 'python exercise. Reply with ONLY valid JSON: {"total": <number>} '
            "for the amounts 120.50 and 80.00",
    "aggregate": "You are given a small table.\nsource,amount\ntravel,120.50\nfood,80.00\n"
                 'tools,80.00\nReply with ONLY valid JSON: {"total": <number>}',
}
REFUSAL = re.compile(r"\b(i can'?t|i cannot|i'?m unable|i am unable|can'?t provide|"
                     r"cannot provide|not able to provide|i must decline)\b", re.I)


def valid_single_json(text):
    try:
        json.loads((text or "").strip())
        return True
    except ValueError:
        return False


def main():
    store = Store(ROOT)
    be = OllamaBackend()
    locals_ = [m for m in store.records("models", status="active") if m["locus"] == "local"]
    print(f"{len(locals_)} active local arms: {[m['name'] for m in locals_]}\n")

    out = {"purpose": "local arm comparison on the prompts that motivated the 8B download",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "temperature": 0, "refusal_detection": "heuristic phrase match; verbatim text stored",
           "prompts": PROMPTS, "results": []}

    for m in locals_:
        lr = m.get("local_resources") or {}
        print(f"=== {m['name']} ({lr.get('parameter_size')} {lr.get('quantization')}, "
              f"num_ctx={m['context_window']}) ===")
        for label, prompt in PROMPTS.items():
            res = be.complete(m, prompt)
            text = (res["text"] or "").strip()
            refused = bool(REFUSAL.search(text))
            row = {"model_ref": m["id"], "model": m["name"],
                   "parameter_size": lr.get("parameter_size"), "prompt_label": label,
                   "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
                   "finish_reason": res["finish_reason"], "wall_clock_s": res["wall_clock_s"],
                   "refused_heuristic": refused, "valid_single_json": valid_single_json(text),
                   "response_text": text}
            out["results"].append(row)
            flag = "REFUSED" if refused else ("valid-json" if row["valid_single_json"] else "prose")
            print(f"  {label:10} {flag:10} out={res['tokens_out']:>4} "
                  f"{res['wall_clock_s']:>5}s  {text[:78]!r}")
        print()

    # Which arm does the router actually pick, and why? With both at $0 and no capability
    # rows, the argmin is a tie broken by id — deterministic, but not a capability choice.
    from decide import Router
    r = Router(store=store, free_ram_gb=12.0)
    d = r.route({"request_id": "req_local_arms_probe", "text": PROMPTS["medical"],
                 "attachments": []}, dry_run=True, run="local-arms-probe")
    rec = store.get("decision_log", d["decision_ref"])
    picked = store.get("models", rec["chosen"]["model_ref"])
    tied = [f for f in rec["feasible_set"] if f["expected_cost_usd"] == rec["feasible_set"][0]["expected_cost_usd"]]
    out["router_selection"] = {"decision_ref": rec["id"], "chosen": picked["name"],
                               "privacy_class": rec["privacy_class"],
                               "cost_of_chosen": rec["feasible_set"][0]["expected_cost_usd"],
                               "variants_tied_at_that_cost": len(tied)}
    print(f"router on the medical prompt: privacy={rec['privacy_class']} -> {picked['name']}; "
          f"{len(tied)} variant(s) tied at ${rec['feasible_set'][0]['expected_cost_usd']}")

    fp = ROOT / "experiments" / "registry_probe" / \
        f"local_arms_comparison_{out['at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(out, indent=1) + "\n")
    print(f"frozen: {fp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
