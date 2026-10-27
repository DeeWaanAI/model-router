#!/usr/bin/env python3
"""Desktop-app leg of acceptance check 7, run against LM Studio's bundled
llama.cpp server (llama.cpp-mac-arm64-apple-metal-advsimd-2.54.0) serving a
Qwen2.5-0.5B GGUF at http://127.0.0.1:1234/v1 — the same address and protocol
LM Studio's desktop server exposes.

The GUI app would not launch headless on this machine (first-run dialogs need a
human), so the backend binary LM Studio itself ships is run directly. Evidence
is written to evidence.json next to this script.
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTER = HERE.parent.parent / "router"
sys.path.insert(0, str(ROUTER))

from acceptance_live import PROMPT, leg  # noqa: E402

LMSTUDIO = {"connector": "openai_compatible", "base_url": "http://127.0.0.1:1234/v1",
            "model": "qwen2.5-0.5b-instruct-q4_k_m.gguf", "locus": "local",
            "privacy_class": "private", "price": (0.0, 0.0),
            "auth": {"scheme": "none", "key_env": None}}


def main():
    print("check 7 desktop-app leg — LM Studio bundled llama.cpp server, port 1234")
    d, a = leg("lmstudio", LMSTUDIO)
    ok = (d["answer"] is not None and d["chosen"]["connector"] == "openai_compatible"
          and a.get("meter") == "provider")
    evidence = {
        "endpoint": LMSTUDIO["base_url"], "model": LMSTUDIO["model"],
        "served_by": "llama-server 2.54.0 (LM Studio bundled backend, Apple Metal arm64)",
        "model_sha256": "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
        "outcome": d["outcome"], "answer": d["answer"],
        "tokens_in": a.get("tokens_in"), "tokens_out": a.get("tokens_out"),
        "meter": a.get("meter"), "actual_spend_usd": d["cost"]["actual_spend_usd"],
        "connector": d["chosen"]["connector"], "pass": ok,
    }
    (HERE / "evidence.json").write_text(json.dumps(evidence, indent=1))
    print("[PASS]" if ok else "[FAIL]", "evidence -> experiments/desktop_app_check/evidence.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
