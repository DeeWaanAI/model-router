"""Cloud backend: Alibaba DashScope (Model Studio), OpenAI-compatible mode.

Added 2026-10-22 because the owner supplied a DashScope key and Together's cheap models
are deprecated rather than merely gated: qwen-flash is callable at $0.05/$0.40 per Mtok,
which gives the cost-quality frontier the cheap cloud arm it was missing (spec §13.5).

Region is measured, not assumed: this key is for the INTERNATIONAL (Singapore) endpoint;
the mainland endpoint rejects it with "Incorrect API key provided" for every model
(experiments/registry_probe/dashscope_availability_*.json).

The key is loaded by explicit path and never printed — name and length only.
"""
import os, time
from pathlib import Path

from . import BackendError
from .http import post_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def load_key(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("DASHSCOPE_API_KEY")
    if env:
        return env.strip()
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("DASHSCOPE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise BackendError("DASHSCOPE_API_KEY not found in the environment or .env")


class DashScopeBackend:
    name = "dashscope"

    def __init__(self, base=DEFAULT_BASE, key=None, timeout=900):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._key = load_key(key)

    def complete(self, model, prompt, expected_out=None):
        body = {"model": model["upstream_id"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": model["output_budget_tokens"],
                "temperature": 0,
                "stream": False}
        body.update(model.get("request_params") or {})

        t0 = time.time()
        status, res = post_json(self.base + "/chat/completions", body,
                                headers={"Authorization": f"Bearer {self._key}"},
                                timeout=self.timeout)
        wall = time.time() - t0

        choices = res.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        usage = res.get("usage") or {}
        return {
            "text": msg.get("content") or "",
            "tokens_in": usage.get("prompt_tokens") or 0,        # provider's own meter
            "tokens_out": usage.get("completion_tokens") or 0,
            "finish_reason": choices[0].get("finish_reason") or "unknown",
            "wall_clock_s": round(wall, 3),
            "confidence": None,                                   # not supplied by DashScope
            "backend": self.name, "http_status": status, "raw": res,
        }
