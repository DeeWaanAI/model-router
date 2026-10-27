#!/usr/bin/env python3
"""connect.py — the user's connect surface: list a provider's models, add one, see what broke.

Owner decision, 2026-10-25: *"the model list should be dynamically updated because each user
has their own providers."* This is that mechanism, and it is deliberately a **human action**,
not a background refresh:

* `--list`   asks the provider what it serves right now (one HTTP GET, no tokens)
* `--add`    writes one connection into `ds:models`
* `--status` re-checks every connection and says OK / auth / unreachable / model-gone
* `--retire-broken` retires the ones confirmed broken — only when you ask, never silently

Routing itself still never fetches anything: a decision is made from the list as it stands.
That keeps the routing path deterministic and auditable while the list tracks the world.

    .venv/bin/python router/connect.py --list \
        --base-url https://api.together.xyz/v1 --key-env TOGETHER_AI_API_KEY
    .venv/bin/python router/connect.py --add zai-org/GLM-5.3-Flash --label "Together GLM" \
        --base-url https://api.together.xyz/v1 --key-env TOGETHER_AI_API_KEY \
        --locus cloud --privacy-class public --price-in 0.15 --price-out 0.50
    .venv/bin/python router/connect.py --status
"""
import argparse, json, sys, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import connections as X  # noqa: E402
import transport  # noqa: E402
from store import Store, new_id, now  # noqa: E402

UA = "deewaanai-l5-router/2.0 (DeeWaanAI OS Layer 5)"
TIMEOUT = 15


def _get(url, key=None, scheme="bearer", header_name=None):
    h = {"Accept": "application/json", "User-Agent": UA}
    if key:
        if scheme == "bearer":
            h["Authorization"] = f"Bearer {key}"
        elif scheme == "x_api_key":
            h["x-api-key"] = key
        else:
            h[header_name or "api-key"] = key
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, ("auth" if e.code in (401, 403) else
                      "not_found" if e.code == 404 else
                      "rate_limit" if e.code == 429 else
                      "server" if e.code >= 500 else "bad_request")
    except Exception as e:                      # noqa: BLE001 — reported, not raised
        return None, f"connection ({type(e).__name__})"


def remote_models(base_url, connector="openai_compatible", key_env=None, scheme="bearer"):
    key = None
    if key_env:
        X.load_env_file(X.ENV_FILE)
        key = __import__("os").environ.get(key_env)
        if not key:
            return None, f"missing_env:{key_env}"
    if connector == "ollama":
        body, err = _get(base_url.rstrip("/") + "/api/tags", key, scheme)
        if err:
            return None, err
        return sorted(m["name"] for m in body.get("models", [])), None
    body, err = _get(base_url.rstrip("/") + "/models", key, scheme)
    if err:
        return None, err
    rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return None, "malformed_response"
    return sorted({m.get("id") for m in rows if m.get("id")}), None


def status_of(conn):
    """A real call, because a listing is not a promise (RC-4.4).

    Being in `/v1/models` does not mean this account may call it: Together still lists the
    70B this layer used in October and rejects it with "Unable to access non-serverless
    model". So the check is a one-token completion with a fixed public payload — a handful of
    tokens per connection, never the user's text (RC-4.8). Local runtimes are checked the same
    way but cost nothing.
    """
    res = transport.call(conn, "ping", 1, sleep=lambda s: None)
    if res["ok"]:
        return "ok", f"answered in {res['latency_ms']} ms"
    detail = res["error_class"]
    if detail in ("model_not_found", "bad_request"):
        ids, err = remote_models(conn["base_url"], conn["connector"],
                                 (conn.get("auth") or {}).get("key_env"),
                                 (conn.get("auth") or {}).get("scheme", "bearer"))
        if not err and conn["model"] not in ids:
            detail = "model_not_found (gone from the provider's list)"
        elif not err:
            detail = f"{detail} (listed but not callable by this account)"
    return "broken", detail


def main(argv=None):
    ap = argparse.ArgumentParser(description="connect a provider, list its models, check health")
    ap.add_argument("--base-url")
    ap.add_argument("--connector", default="openai_compatible",
                    choices=["openai_compatible", "ollama"])
    ap.add_argument("--key-env", default=None,
                    help="NAME of the environment variable holding the key; never the key")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", metavar="MODEL_ID")
    ap.add_argument("--label")
    ap.add_argument("--locus", choices=["local", "cloud"])
    ap.add_argument("--privacy-class", default="private",
                    choices=["public", "confidential", "private", "P0", "P1", "P2"])
    ap.add_argument("--price-in", type=float, default=None)
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--context-window", type=int, default=32768)
    ap.add_argument("--set-privacy", metavar="LABEL_OR_ID",
                    help="change what one connection is allowed to see (writes a successor)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--retire-broken", action="store_true")
    a = ap.parse_args(argv)

    store = Store(ROOT)
    X.load_env_file(X.ENV_FILE)

    if a.list or a.add:
        if not a.base_url:
            raise SystemExit("--base-url is required for --list / --add")
        ids, err = remote_models(a.base_url, a.connector, a.key_env)
        if err:
            print(f"[FAIL] {err}"); return 1
        print(f"{len(ids)} models offered by {a.base_url}:")
        for m in ids:
            print(f"  {m}")
        if a.list and not a.add:
            return 0
        if a.add not in ids:
            print(f"[FAIL] {a.add!r} is not in that list; nothing written"); return 1

        pcls = X.PRIVACY_ALIASES[a.privacy_class]
        unknown = a.price_in is None or a.price_out is None
        rec = {"id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
               "status": "active", "superseded_by": None, "source_ref": ["user"],
               "label": a.label or a.add, "connector": a.connector,
               "base_url": a.base_url, "model": a.add,
               "auth": {"scheme": "bearer" if a.key_env else "none", "key_env": a.key_env},
               "locus": a.locus or ("local" if a.connector == "ollama" else "cloud"),
               "privacy_class": pcls,
               "price": {"input_per_mtok": a.price_in or 0.0,
                         "output_per_mtok": a.price_out or 0.0,
                         "source": "unknown" if unknown else "user_declared"},
               "context_window": a.context_window,
               "output_budget_tokens": 1024, "tokenizer_ref": None,
               "request_params": None, "timeout_s": 60.0, "local_resources": None,
               "health": None, "declared_at": now()}
        store.append("models", rec)
        print(f"[OK] added {rec['id']} {rec['label']!r} ({rec['connector']}, {rec['locus']})"
              + ("  price marked unknown — it will rank last" if unknown else ""))
        return 0

    if a.set_privacy:
        rec = next((m for m in store.records("models", status="active")
                    if m["id"] == a.set_privacy or m.get("label") == a.set_privacy), None)
        if rec is None:
            print(f"[FAIL] no active connection {a.set_privacy!r}"); return 1
        new = json.loads(json.dumps(rec))
        new.update(id=new_id("mdl"), created_at=now(), superseded_by=None,
                   source_ref=[rec["id"]], declared_at=now(),
                   privacy_class=X.PRIVACY_ALIASES[a.privacy_class])
        store.append("models", new)
        store.set_status("models", rec["id"], "retired", superseded_by=new["id"])
        print(f"[OK] {rec.get('label')!r} may now see up to "
              f"{new['privacy_class']!r} content (was {rec.get('privacy_class')!r})")
        return 0

    if a.status or a.retire_broken:
        active = [m for m in store.records("models", status="active") if "connector" in m]
        broken = []
        for m in active:
            conn, probs = X.normalise(m)
            if conn is None:
                print(f"  {m.get('label'):34} BROKEN (record) {probs}")
                broken.append((m, "invalid_record"))
                continue
            state, why = status_of(conn)
            print(f"  {m['label']:34} {state.upper():6} {why}")
            if state == "broken":
                broken.append((m, why))
        if a.retire_broken:
            for m, why in broken:
                store.set_status("models", m["id"], "retired", superseded_by=None)
                print(f"  retired {m['label']!r}: {why}")
        elif broken:
            print(f"\n{len(broken)} broken. Re-run with --retire-broken to retire them "
                  f"(nothing is removed silently).")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
