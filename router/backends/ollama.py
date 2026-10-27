#!/usr/bin/env python3
"""ollama — the native /api/chat path, kept because its accounting and options differ.

Two options must come from the connection record and must never be defaulted:
`num_ctx` (Ollama's own default silently truncates long prompts, so a gate-3 pass would
describe a fit the runtime never honoured) and `num_predict` (so the budget a capability
row is scoped to is the budget actually enforced, and `finish_reason: length` means what
the escalation trigger thinks it means).
"""

ACCEPTED_DONE = {"stop", "length", "content_filter", None}


def payload(conn, prompt, expected_out):
    opts = {"temperature": 0,
            "num_ctx": int(conn["context_window"]),
            "num_predict": int(expected_out or conn.get("output_budget_tokens") or 1024)}
    opts.update(conn.get("request_params") or {})
    return {"model": conn["model"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": opts}


def parse(conn, body):
    try:
        text = body["message"]["content"]
    except (KeyError, TypeError):
        return None
    if not isinstance(text, str):
        return None
    done = body.get("done_reason")
    if done not in ACCEPTED_DONE:
        return None
    tin, tout = body.get("prompt_eval_count"), body.get("eval_count")
    return {"text": text, "finish_reason": done,
            "tokens_in": tin, "tokens_out": tout,
            "meter": "provider" if tin is not None and tout is not None else "absent"}
