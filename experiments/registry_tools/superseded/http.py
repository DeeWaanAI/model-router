"""Shared HTTP for backends.

The User-Agent header is not decoration: Together's API returns HTTP 403 for the default
Python urllib agent and 200 for an explicit one (spec §13.2, finding 1). Every request
from this layer sets it.
"""
import json, urllib.error, urllib.request

UA = "deewaanai-l5-router/1.0 (DeeWaanAI OS Layer 5; see spec.md)"


def post_json(url, payload, headers=None, timeout=900):
    from . import BackendError
    h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        # The body is quoted for diagnosis; it never contains our key, and we never echo
        # the request headers back into an error.
        raise BackendError(f"HTTP {e.code} {e.reason} from {url}: {body}") from None
    except urllib.error.URLError as e:
        raise BackendError(f"cannot reach {url}: {e.reason}") from None
    except TimeoutError as e:
        raise BackendError(f"timeout after {timeout}s calling {url}") from e
