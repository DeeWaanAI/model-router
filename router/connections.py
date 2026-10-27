#!/usr/bin/env python3
"""connections.py — the user's connect list, and nothing else (spec §14, RC-1).

The router does not discover, suggest or catalogue models. `ds:models` holds exactly what
the user connected; each record is one connection to one endpoint. This module's whole job
is to turn those records into callable objects, and to be honest about what is declared
rather than measured.

Three rules are enforced here rather than in prose:

* **No secret is ever stored.** A record carries `auth.key_env`, the NAME of an environment
  variable. Resolution happens at call time; a missing variable is reported as
  `missing_env:<NAME>` on that connection and cannot silently become None (RC-1.2).
* **Declarations stay declarations.** `locus` and `privacy_class` come from the user. They
  are trusted for routing and labelled `user_declared` in the decision record; nothing here
  guesses from the URL (RC-1.3). `looks_non_loopback` only *warns*, it never overrides.
* **A broken entry is not a broken boot.** Every record is validated independently and the
  failures are returned, so one malformed line leaves the rest routable (RC-4.11).
"""
import ipaddress, os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# The owner's example uses a P-scale; this layer's closed enum is shared with Layers 1-4,
# so P-levels are accepted as input aliases and normalised on read (spec §14, delta D-3).
PRIVACY_ALIASES = {"P0": "private", "P1": "confidential", "P2": "public",
                   "private": "private", "confidential": "confidential", "public": "public"}

REQUIRED = ("id", "label", "connector", "base_url", "model", "locus", "privacy_class",
            "price", "context_window")

CONNECTORS = ("openai_compatible", "ollama")


class ConnectionError_(Exception):
    pass


def _loopback(url):
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return None
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def declared_view(rec):
    """What the record *says*, for the decision log. Never what we inferred."""
    return {"locus": "user_declared", "privacy_class": "user_declared",
            "price": (rec.get("price") or {}).get("source", "unknown"),
            "endpoint": "user_declared"}


def model_key(base, mode=None):
    """The identity a measurement attaches to.

    A provider can serve the same model id in more than one mode (Together: chat and
    reasoning), and a chat measurement must never be credited to the reasoning connection.
    The mode is therefore part of the key when it is anything other than plain chat.
    """
    key = str(base or "")
    if "#" in key:
        return key
    if mode and mode not in ("chat", ""):
        return f"{key}#{mode}"
    return key


def normalise(rec):
    """Validate one connection record. Returns (connection, problems) — problems non-empty
    means the connection is unusable, not that the caller should abort."""
    problems = []
    for f in REQUIRED:
        if rec.get(f) in (None, ""):
            problems.append(f"missing_field:{f}")
    if problems:
        return None, problems

    if rec["connector"] not in CONNECTORS:
        problems.append(f"unknown_connector:{rec['connector']}")
    if rec["locus"] not in ("local", "cloud"):
        problems.append(f"bad_locus:{rec['locus']}")
    pcls = PRIVACY_ALIASES.get(str(rec["privacy_class"]))
    if pcls is None:
        problems.append(f"bad_privacy_class:{rec['privacy_class']}")
    price = rec.get("price") or {}
    for k in ("input_per_mtok", "output_per_mtok"):
        v = price.get(k)
        if v is None:
            problems.append(f"price_missing:{k}")
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            problems.append(f"price_not_number:{k}")
        elif v < 0:
            problems.append(f"price_negative:{k}")
        elif v > 1000.0:
            problems.append(f"price_suspected_unit_error:{k}={v}")
    if not isinstance(rec.get("context_window"), int) or rec["context_window"] <= 0:
        problems.append(f"bad_context_window:{rec.get('context_window')}")
    if problems:
        return None, problems

    auth = rec.get("auth") or {"scheme": "none", "key_env": None}
    scheme = auth.get("scheme", "none")
    if scheme not in ("bearer", "header", "x_api_key", "none"):
        problems.append(f"bad_auth_scheme:{scheme}")
    key = None
    if scheme != "none":
        env = auth.get("key_env")
        if not env:
            problems.append("auth_scheme_requires_key_env")
        elif not os.environ.get(env):
            # Reported, never resolved to a placeholder: the connection is broken and says why.
            problems.append(f"missing_env:{env}")
        else:
            key = os.environ[env]
    if problems:
        return None, problems

    conn = dict(rec)
    conn["privacy_class"] = pcls
    conn["auth"] = {**auth, "scheme": scheme}
    conn["_key"] = key
    conn["price"] = {"input_per_mtok": float(price["input_per_mtok"]),
                     "output_per_mtok": float(price["output_per_mtok"]),
                     "source": price.get("source", "user_declared")}
    conn["cost_rankable"] = conn["price"]["source"] != "unknown"
    conn["timeout_s"] = float(rec.get("timeout_s") or 60.0)
    conn["connect_timeout_s"] = float(rec.get("connect_timeout_s") or 10.0)
    conn["output_budget_tokens"] = int(rec.get("output_budget_tokens") or 1024)
    conn["capability_key"] = model_key(rec.get("capability_key") or rec["model"],
                                        rec.get("mode"))
    conn["warnings"] = []
    if conn["locus"] == "local" and _loopback(conn["base_url"]) is False:
        # Warn only. The user may have a local network box; overriding their declaration
        # would be the guessing RC-1.3 forbids.
        conn["warnings"].append("locus_declared_local_but_base_url_is_not_loopback")
    return conn, []


def load_env_file(path):
    """Read KEY=VALUE lines from an explicit path into os.environ.

    The path is fixed and gitignored; nothing is inferred from the working directory, and an
    existing environment variable always wins (so a shell override beats the file). Values are
    never logged — only the names are ever reported.
    """
    p = Path(path)
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def load(store, env_file=None):
    """Return (connections, problems). Never raises for a bad record (RC-4.11).

    A connection whose key variable is unset is NOT routable — it cannot work — so it is
    reported with `missing_env:<NAME>` rather than carried as a half-object. The rest of the
    list still boots (RC-1.2 + RC-4.11).
    """
    if env_file is not None:
        load_env_file(env_file)
    conns, problems = [], []
    for rec in store.records("models", status="active"):
        if "connector" not in rec:
            continue                      # pre-RC row kept for historical replay only
        conn, probs = normalise(rec)
        if conn is None:
            problems.append({"id": rec.get("id"), "label": rec.get("label"),
                             "problems": probs})
        else:
            conns.append(conn)
    return conns, problems


def capability_key(store, model_ref):
    """Capabilities attach to WHAT ANSWERS, not to a registry row.

    A connection record may be replaced (new id) while the model behind it is the same.
    Capability rows written before RC-1 carry a `model_ref` and no `model_key`; resolving
    through the old record's `upstream_id` keeps that evidence attached instead of
    silently orphaning it.
    """
    m = store.get("models", model_ref)
    if not m:
        return None
    return model_key(m.get("capability_key") or m.get("upstream_id") or m.get("model"),
                     m.get("mode"))


def auth_headers(conn):
    scheme = (conn.get("auth") or {}).get("scheme", "none")
    key = conn.get("_key")
    if scheme == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if scheme == "x_api_key":
        return {"x-api-key": key}
    if scheme == "header":
        name = (conn.get("auth") or {}).get("header_name") or "api-key"
        return {name: key}
    return {}
