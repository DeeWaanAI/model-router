"""Cloud backend: Together AI (spec §5).

The API key is loaded by explicit path and is never printed, logged, or written into a
dataset — variable name and length only (HANDOFF §7). This code goes on a public
AGPL-3.0 repo, so a leaked key is unrecoverable.

`max_tokens` comes from the registry record's output_budget_tokens, and any
provider-specific body parameters come from the record's `request_params` — provider
quirks are data, not code (spec §13.1).
"""
import os, time
from pathlib import Path

from . import BackendError
from .http import post_json

ROOT = Path(__file__).resolve().parents[2]


def load_key(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("TOGETHER_AI_API_KEY")
    if env:
        return env.strip()
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("TOGETHER_AI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise BackendError("TOGETHER_AI_API_KEY not found in the environment or .env")


class TogetherBackend:
    name = "together"

    def __init__(self, base="https://api.together.xyz/v1", key=None, timeout=900):
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
            "confidence": None,                                   # not supplied by Together
            "reasoning_text_present": bool(msg.get("reasoning_content")),
            "backend": self.name, "http_status": status, "raw": res,
        }
