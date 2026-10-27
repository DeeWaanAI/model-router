#!/usr/bin/env python3
"""health.py — a dead endpoint must not cost every request a timeout (spec §14, RC-4.3).

Per connection: after 3 consecutive TRANSIENT failures the connection is marked unhealthy
for 60 s. While unhealthy it is excluded from the feasible set with
`excluded_reason: "unhealthy"` and costs nothing. After the cooldown it becomes half-open:
the next real call decides — success closes it, failure re-opens it. Only a real call can
make a connection available (RC-4.4), so there is no "ping everything at boot" behaviour
that would spend the user's tokens to learn what the first request will tell us anyway.

Auth and bad_request failures never open the breaker: repeating them fixes nothing, and a
misconfigured key should be reported to the user on every request, not silently hidden for
a minute.
"""
import threading
import time

FAILURE_THRESHOLD = 3
COOLDOWN_S = 60.0
BREAKING = frozenset({"timeout", "rate_limit", "server", "connection"})


class Health:
    def __init__(self, threshold=FAILURE_THRESHOLD, cooldown_s=COOLDOWN_S, clock=time.time):
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self._lock = threading.Lock()
        self._state = {}

    def _s(self, cid):
        return self._state.setdefault(cid, {"failures": 0, "opened_at": None,
                                           "last_error": None, "last_ok_at": None})

    def status(self, cid):
        with self._lock:
            s = self._s(cid)
            if s["opened_at"] is None:
                return "closed"
            if self.clock() - s["opened_at"] >= self.cooldown_s:
                return "half_open"
            return "open"

    def unavailable(self):
        """Ids excluded right now. Half-open ids are admitted: the next call is the probe."""
        with self._lock:
            now = self.clock()
            return sorted(cid for cid, s in self._state.items()
                          if s["opened_at"] is not None
                          and now - s["opened_at"] < self.cooldown_s)

    def record_success(self, cid):
        with self._lock:
            s = self._s(cid)
            s.update(failures=0, opened_at=None, last_error=None, last_ok_at=self.clock())

    def record_failure(self, cid, error_class):
        """Returns the new status. Non-breaking classes reset nothing but do not open either."""
        with self._lock:
            s = self._s(cid)
            s["last_error"] = error_class
            if error_class not in BREAKING:
                return "closed" if s["opened_at"] is None else "open"
            s["failures"] += 1
            if s["failures"] >= self.threshold:
                s["opened_at"] = self.clock()
                return "open"
            return "closed" if s["opened_at"] is None else "open"

    def snapshot(self):
        """Pinned into the decision record so gate replay is exact (RC-4.3)."""
        return {"unhealthy": self.unavailable(),
                "threshold": self.threshold, "cooldown_s": self.cooldown_s,
                "states": {cid: self.status(cid) for cid in sorted(self._state)}}
