"""Connector dispatch (spec §14, RC-2).

Exactly two wire formats exist in this layer, selected by the connection's `connector`
field — never by provider name, base URL, or any other heuristic:

    openai_compatible   POST {base_url}/chat/completions
    ollama              POST {base_url}/api/chat

Everything else — timeouts, retries, typed errors, usage accounting — lives in
`transport.py` and is shared. There is no per-provider branch in this package, which is
what makes "add a new desktop app" a data edit instead of a code change.
"""
from . import ollama, openai_compatible

CONNECTORS = {"openai_compatible": openai_compatible, "ollama": ollama}


class BackendError(Exception):
    pass


def _mod(conn):
    m = CONNECTORS.get(conn.get("connector"))
    if m is None:
        raise BackendError(f"unknown connector {conn.get('connector')!r}")
    return m


def payload_for(conn, prompt, expected_out):
    return _mod(conn).payload(conn, prompt, expected_out)


def parse_for(conn, body):
    return _mod(conn).parse(conn, body)
