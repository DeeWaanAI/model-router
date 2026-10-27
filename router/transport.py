#!/usr/bin/env python3
"""transport.py — one HTTP path for both connectors, with the failure behaviour the owner
required (spec §14, RC-4).

A broken connection must degrade the answer, never the router. That is implemented, not
asserted:

* two declared timeouts: `connect_timeout_s` bounds reaching the endpoint, `timeout_s`
  bounds the whole exchange (RC-4.1);
* at most 2 retries, exponential backoff with jitter, and ONLY for transient classes;
* `auth`, `bad_request` and `model_not_found` are never retried — repetition cannot fix
  them (RC-4.2);
* every try is recorded with its own usage, so a retry can never double-count cost;
* anything unexpected in the response becomes `malformed_response`, a typed value — it
  never raises out of `route()` (RC-4.9).
"""
import http.client, json, random, socket, time
from urllib.parse import urlparse

ERROR_CLASSES = ("auth", "rate_limit", "timeout", "connection", "model_not_found",
                 "bad_request", "server", "malformed_response")
TRANSIENT = frozenset({"timeout", "rate_limit", "server", "connection"})
MAX_RETRIES = 2
UA = "deewaanai-l5-router/2.0 (DeeWaanAI OS Layer 5; see spec.md)"


def classify_status(code):
    if code in (401, 403):
        return "auth"
    if code == 404:
        return "model_not_found"
    if code in (408, 504):
        return "timeout"
    if code == 429:
        return "rate_limit"
    if 400 <= code < 500:
        return "bad_request"
    return "server"


def _once(conn, payload, headers, path):
    """One HTTP attempt. Returns dict; never raises."""
    url = conn["base_url"].rstrip("/") + path
    u = urlparse(url)
    timeout = float(conn["timeout_s"])
    connect_timeout = float(conn["connect_timeout_s"])
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA}
    h.update(headers or {})
    t0 = time.time()
    c = cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=connect_timeout)
    try:
        c.connect()
        c.sock.settimeout(timeout)
        c.request("POST", u.path + (("?" + u.query) if u.query else ""),
                  body=json.dumps(payload).encode("utf-8"), headers=h)
        r = c.getresponse()
        raw = r.read().decode("utf-8", "replace")
        latency = int((time.time() - t0) * 1000)
        if r.status >= 400:
            return {"ok": False, "error_class": classify_status(r.status), "status": r.status,
                    "detail": raw[:300], "latency_ms": latency, "body": None}
        try:
            body = json.loads(raw)
        except ValueError:
            return {"ok": False, "error_class": "malformed_response", "status": r.status,
                    "detail": raw[:200], "latency_ms": latency, "body": None}
        return {"ok": True, "error_class": None, "status": r.status, "detail": "",
                "latency_ms": latency, "body": body}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "error_class": "timeout", "status": None,
                "detail": f"timeout after {timeout}s", "latency_ms":
                int((time.time() - t0) * 1000), "body": None}
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, OSError) as e:
        return {"ok": False, "error_class": "connection", "status": None,
                "detail": str(e)[:200], "latency_ms": int((time.time() - t0) * 1000),
                "body": None}
    except http.client.HTTPException as e:
        return {"ok": False, "error_class": "malformed_response", "status": None,
                "detail": f"{type(e).__name__}: {e}"[:200],
                "latency_ms": int((time.time() - t0) * 1000), "body": None}
    finally:
        try:
            c.close()
        except Exception:
            pass


def call(conn, prompt, expected_out, sleep=time.sleep, rng=random.random):
    """One logical call, including retries. Returns the result plus the full try chain."""
    path = "/chat/completions" if conn["connector"] == "openai_compatible" else "/api/chat"
    from backends import payload_for, parse_for   # local import: connectors own their shape
    payload = payload_for(conn, prompt, expected_out)
    from connections import auth_headers
    headers = auth_headers(conn)

    tries, last, t0 = [], None, time.time()
    for n in range(MAX_RETRIES + 1):
        res = _once(conn, payload, headers, path)
        usage = parse_for(conn, res["body"]) if res.get("ok") else None
        if res.get("ok") and usage is None:
            res = {"ok": False, "error_class": "malformed_response", "status": res["status"],
                   "detail": "response missing choices/message content",
                   "latency_ms": res["latency_ms"], "body": None}
        tries.append({"n": n + 1, "error_class": res["error_class"],
                      "status": res.get("status"), "latency_ms": res["latency_ms"],
                      "usage": ({"tokens_in": usage["tokens_in"],
                                 "tokens_out": usage["tokens_out"],
                                 "meter": usage["meter"]} if usage else None),
                      "detail": res.get("detail", "")[:160]})
        last = res
        if res["ok"]:
            break
        if res["error_class"] not in TRANSIENT or n == MAX_RETRIES:
            break
        # exponential backoff with jitter; ceiling kept small so a request never hangs for
        # tens of seconds just to learn that a desktop app is closed.
        sleep(min(4.0, 0.4 * (2 ** n) * (0.5 + rng())))
    out = {"ok": bool(last and last["ok"]), "error_class": last["error_class"] if last else "server",
           "tries": tries, "latency_ms": int((time.time() - t0) * 1000),
           "text": None, "tokens_in": None, "tokens_out": None, "finish_reason": None,
           "meter": None, "connector": conn["connector"]}
    if out["ok"]:
        usage = parse_for(conn, last["body"])
        out.update(text=usage["text"], tokens_in=usage["tokens_in"],
                   tokens_out=usage["tokens_out"], finish_reason=usage["finish_reason"],
                   meter=usage["meter"])
    return out


def probe(conn):
    """Health check with a FIXED PUBLIC payload — never the user's prompt (RC-4.8)."""
    return call(conn, "ping", 1, sleep=lambda s: None)
