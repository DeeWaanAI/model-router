#!/usr/bin/env python3
"""Probe the local Ollama arm and freeze the raw result as registry provenance.

Measures what ds:models needs and cannot be assumed: the quantization and parameter
count the server reports, the runtime context window actually applied, and inference
speed / token counts from Ollama's OWN meter (spec §1.6: actuals are provider-metered,
never estimated). No cloud calls, no cost.
"""
import argparse, json, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:11434"
UA = "deewaanai-l5-router/1.0 (registry probe)"


def post(path, payload, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(model, num_ctx):
    try:
        show = post("/api/show", {"model": model})
    except urllib.error.URLError as e:
        print(f"ollama server not reachable at {BASE}: {e}\n"
              f"start it with: nohup /opt/homebrew/bin/ollama serve &")
        return 2

    info = {
        "model": model,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "details": show.get("details"),
        "model_info": {k: v for k, v in (show.get("model_info") or {}).items()
                       if any(t in k for t in ("parameter_size", "quantization", "context_length",
                                               "architecture", "embedding_length", "layers"))},
        "parameters_declared": show.get("parameters"),
        "requested_num_ctx": num_ctx,
    }

    prompt = ("You are given a small table.\nsource,amount\ntravel,120.50\nfood,80.00\n"
              "Reply with ONLY valid JSON: {\"total\": <number>}")
    t0 = time.time()
    gen = post("/api/generate", {
        "model": model, "prompt": prompt, "stream": False, "temperature": 0,
        "options": {"num_ctx": num_ctx, "temperature": 0},
    })
    wall = time.time() - t0

    info["probe_call"] = {
        "wall_clock_s": round(wall, 3),
        "prompt_eval_count": gen.get("prompt_eval_count"),
        "eval_count": gen.get("eval_count"),
        "prompt_eval_duration_ns": gen.get("prompt_eval_duration"),
        "eval_duration_ns": gen.get("eval_duration"),
        "finish_reason": gen.get("done_reason"),
        "response_chars": len(gen.get("response") or ""),
        "response_is_json_only": (gen.get("response") or "").strip().startswith("{"),
    }
    pe, ed = gen.get("prompt_eval_duration"), gen.get("eval_duration")
    if pe and gen.get("prompt_eval_count"):
        info["probe_call"]["prompt_tokens_per_s"] = round(gen["prompt_eval_count"] / (pe / 1e9), 1)
    if ed and gen.get("eval_count"):
        info["probe_call"]["output_tokens_per_s"] = round(gen["eval_count"] / (ed / 1e9), 1)
    info["probe_call"]["response_text"] = gen.get("response")

    out = ROOT / "experiments" / "registry_probe"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / f"local_{model.replace(':', '_')}_{info['probed_at'].replace(':', '').replace('-', '')}.json"
    fp.write_text(json.dumps(info, indent=1) + "\n")

    d = info["details"] or {}
    print(f"frozen: {fp.relative_to(ROOT)}")
    print(f"details: family={d.get('family')} params={d.get('parameter_size')} quant={d.get('quantization_level')}")
    print(f"model_info context_length={info['model_info'].get('llama.context_length')}")
    print(f"requested num_ctx={num_ctx}")
    pc = info["probe_call"]
    print(f"probe: wall={pc['wall_clock_s']}s prompt_tokens={pc['prompt_eval_count']} "
          f"output_tokens={pc['eval_count']} finish={pc['finish_reason']}")
    print(f"speed: prompt={pc.get('prompt_tokens_per_s')} tok/s output={pc.get('output_tokens_per_s')} tok/s")
    print(f"response: {pc['response_text']!r}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="llama3.2:latest")
    ap.add_argument("--num-ctx", type=int, default=8192,
                    help="runtime context window to register; the model max is usually larger")
    a = ap.parse_args()
    sys.exit(main(a.model, a.num_ctx))
