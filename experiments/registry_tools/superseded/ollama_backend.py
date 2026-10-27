"""Local backend: Ollama (spec §5).

Two options are set from the registry record on every call and must not be defaulted:

* `num_ctx` = the record's context_window. Ollama otherwise applies its own default,
  which silently truncates a long prompt — and gate 3 would then have certified a fit
  that the runtime never honoured.
* `num_predict` = the record's output_budget_tokens, so the budget the capability rows
  are scoped to (spec §1.3) is the budget actually enforced, and `finish_reason: length`
  means what the escalation trigger thinks it means.
"""
import subprocess, time, urllib.error, urllib.request

from .http import post_json

DEFAULT_BASE = "http://127.0.0.1:11434"
BINARY = "/opt/homebrew/bin/ollama"


def _alive(base):
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_server(base=DEFAULT_BASE, binary=BINARY, wait_s=40):
    """Start a SESSION-SCOPED server if nothing is listening, and wait until it answers.

    `start_new_session=True` detaches it from this shell's process group, which is why the
    server kept dying between tool calls before. It deliberately does NOT make Ollama
    persistent (`brew services start ollama`) — HANDOFF §3 requires the owner's go for that.
    """
    if _alive(base):
        return True, "already running"
    subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if _alive(base):
            return True, "started (session-scoped, detached)"
        time.sleep(1)
    return False, f"server did not answer within {wait_s}s"


class OllamaBackend:
    name = "ollama"

    def __init__(self, base=DEFAULT_BASE, timeout=1800, autostart=True):
        self.base = base.rstrip("/")
        self.timeout = timeout
        if autostart:
            ok, detail = ensure_server(self.base)
            if not ok:
                raise RuntimeError(f"ollama unavailable: {detail}")

    def complete(self, model, prompt, expected_out=None):
        rp = model.get("request_params") or {}
        options = {"temperature": 0, "num_ctx": model["context_window"],
                   "num_predict": model["output_budget_tokens"]}
        options.update(rp.get("options") or {})
        payload = {"model": model["upstream_id"], "prompt": prompt, "stream": False,
                   "options": options}
        payload.update({k: v for k, v in rp.items() if k != "options"})

        t0 = time.time()
        status, res = post_json(self.base + "/api/generate", payload, timeout=self.timeout)
        wall = time.time() - t0

        # Ollama reports done_reason "stop" | "length"; "length" means num_predict was hit,
        # which is exactly the non-termination hazard the escalation trigger listens for.
        done = res.get("done_reason") or ("stop" if res.get("done") else "unknown")
        return {
            "text": res.get("response") or "",
            "tokens_in": res.get("prompt_eval_count") or 0,      # provider's own meter
            "tokens_out": res.get("eval_count") or 0,
            "finish_reason": done,
            "wall_clock_s": round(wall, 3),
            "confidence": None,                                   # not supplied by Ollama
            "backend": self.name, "http_status": status, "raw": res,
        }
