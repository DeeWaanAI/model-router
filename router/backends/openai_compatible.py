#!/usr/bin/env python3
"""openai_compatible — ONE class for every endpoint that speaks the OpenAI chat protocol.

That is not a shortcut: LM Studio, Jan, GPT4All, Msty, llama.cpp's server, vLLM, Ollama's
/v1 surface, and the paid APIs this layer has actually billed (Together, DashScope) all
implement it. Two provider quirks used to live in `together.py` and `dashscope.py`; they are
now data on the connection (`request_params`, `auth.scheme`), because a quirk that needs
code is a named limitation, not a special case (RC-2.2/2.3).
"""

ACCEPTED_FINISH = {"stop", "length", "content_filter", "tool_calls", None}


def payload(conn, prompt, expected_out):
    p = {"model": conn["model"],
         "messages": [{"role": "user", "content": prompt}],
         "max_tokens": expected_out,
         "temperature": 0,
         "stream": False}
    p.update(conn.get("request_params") or {})   # quirks are data, never branches
    return p


def parse(conn, body):
    """Returns the normalised answer, or None if the body is not the shape we asked for."""
    try:
        choice = body["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(text, str):
        return None
    finish = choice.get("finish_reason")
    if finish not in ACCEPTED_FINISH:
        return None                                  # typed by the caller as malformed (RC-4.9)
    usage = body.get("usage") or {}
    tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
    return {"text": text, "finish_reason": finish,
            "tokens_in": tin, "tokens_out": tout,
            "meter": "provider" if tin is not None and tout is not None else "absent"}
