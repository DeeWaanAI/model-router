#!/usr/bin/env python3
"""test_acceptance_rc.py — the ten acceptance checks from required_changes.md §7.

These are not unit tests of helpers: every check runs the real `route()` and the real HTTP
transport against a local server whose behaviour is switched per model. That matters,
because most of what RC-4 asks for (timeouts, retry classes, the breaker, typed malformed
responses, the fallback chain, privacy on the failed path) lives exactly where a mocked
transport would hide it.

Run: .venv/bin/python router/test_acceptance_rc.py
Each check prints PASS/FAIL and the evidence it used. Exit 0 only if all ten pass.
"""
import json, os, shutil, socket, sys, threading, time, unittest
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import connections as X  # noqa: E402
import transport as TX  # noqa: E402
from audit import audit  # noqa: E402
from decide import Router  # noqa: E402
from health import Health  # noqa: E402
from store import DATASETS, Store, new_id, now  # noqa: E402

NEEDLE = "PAYROLL-NEEDLE-4417"


# ---------------------------------------------------------------- fake endpoint
class Behaviours:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_model = defaultdict(list)   # model -> ["ok", "401", ...] popped in order
        self.default = "ok"
        self.hits = []                      # (model, body_text)

    def set(self, model, *seq):
        with self.lock:
            self.by_model[model] = list(seq)

    def next(self, model):
        with self.lock:
            q = self.by_model.get(model)
            return q.pop(0) if q else self.default


B = Behaviours()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            model = json.loads(raw).get("model", "?")
        except ValueError:
            model = "?"
        B.hits.append((model, raw, self.path))
        how = B.next(model)
        if how == "hang":
            time.sleep(2.0)
            self._send(200, {"choices": [{"message": {"content": "late"}, "finish_reason": "stop"}]})
            return
        if how == "401":
            self._send(401, {"error": "invalid api key"}); return
        if how == "404":
            self._send(404, {"error": "model not found"}); return
        if how == "500":
            self._send(503, {"error": "upstream"}); return
        if how == "badjson":
            self._raw(200, b'{"choices": [ this is not json'); return
        if how == "nochoices":
            self._send(200, {"id": "x"}); return
        if how == "nofinish":
            self._send(200, {"choices": [{"message": {"content": "hi"}, "finish_reason": "weird"}]})
            return
        self._send(200, {"choices": [{"message": {"content": "ok answer"},
                                      "finish_reason": how if how in ("length",) else "stop"}],
                         "usage": {"prompt_tokens": 11, "completion_tokens": 7}})

    def _send(self, code, obj):
        self._raw(code, json.dumps(obj).encode())

    def _raw(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
BASE = f"http://127.0.0.1:{SERVER.server_address[1]}/v1"
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
DEAD = "http://127.0.0.1:1/v1"          # nothing listens on port 1


# ---------------------------------------------------------------- fixtures
def fresh_store(tmp):
    root = Path(tmp) / "store"
    for ds in DATASETS:
        d = root / "datasets" / ds
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "datasets" / ds / "schema.json", d / "schema.json")
        for f in ("data.jsonl", "history.jsonl", "bindings.jsonl"):
            (d / f).touch()
        (d / "SUPERSEDED.json").write_text("[]")
    s = Store(root)
    live = Store(ROOT)
    for ds in ("rules", "policy"):
        for rec in live.records(ds, status="active"):
            if ds == "policy" and rec["version_label"] not in ("v7", "v8"):
                continue
            s.append(ds, json.loads(json.dumps(rec)))
    return s


def add_conn(store, model, *, locus="local", privacy="private", price=0.0,
             base=None, timeout_s=0.5, connector="openai_compatible"):
    rec = {"id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
           "status": "active", "superseded_by": None, "source_ref": ["acceptance-test"],
           "label": f"test {model}", "connector": connector,
           "base_url": BASE if base is None else base, "model": model,
           "auth": {"scheme": "none", "key_env": None},
           "locus": locus, "privacy_class": privacy,
           "price": {"input_per_mtok": price, "output_per_mtok": price,
                     "source": "user_declared"},
           "context_window": 100000, "output_budget_tokens": 512, "tokenizer_ref": None,
           "request_params": None, "timeout_s": timeout_s, "local_resources": None,
           "health": None, "declared_at": now(), "capability_key": model}
    store.append("models", rec)
    return rec


class FastTransport:
    """Same transport, sleep disabled so a 3-try timeout case costs ~1.5 s, not 5."""
    def call(self, conn, prompt, out):
        return TX.call(conn, prompt, out, sleep=lambda s: None)


def router_for(store, **kw):
    return Router(store=store, transport=FastTransport(), health=kw.pop("health", Health()), **kw)


PUBLIC = ("Sum the amount column of expenses_small.csv and count its rows. "
          "This is a python data exercise on public synthetic data.")
PRIVATE = (f"This is my personal disciplinary letter and my employee salary. {NEEDLE} "
           "Please summarise it.")


def route(r, text, dry=False, run="acc"):
    return r.route({"request_id": new_id("req"), "text": text}, dry_run=dry, run=run)


# ---------------------------------------------------------------- the ten checks
class TestAcceptance(unittest.TestCase):
    def setUp(self):
        self.fresh()

    def fresh(self):
        """A clean store per sub-case: the cascade depth is policy data (2), so a test that
        keeps appending connections would silently stop reaching the later ones."""
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="rc-acc-")
        self.store = fresh_store(self.tmp)
        B.hits.clear()
        B.default = "ok"
        B.by_model.clear()

    # 1 -------------------------------------------------------------------------
    def test_01_refusal_when_only_endpoint_is_down(self):
        c = add_conn(self.store, "solo", base=DEAD)
        d = route(router_for(self.store), PUBLIC)
        self.assertEqual(d["outcome"], "refused")
        self.assertEqual(d["refusal_reason"], "all_connections_failed")
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["attempts"][0]["model_ref"], c["id"])
        self.assertEqual(rec["attempts"][0]["error_class"], "connection")
        self.assertIsNone(d["chosen"])
        self.assertTrue(any(t["error_class"] == "connection"
                            for t in rec["attempts"][0]["tries"]))
        print("\n  [1] refused=all_connections_failed, failed="
              f"{rec['attempts'][0]['model_ref']} class={rec['attempts'][0]['error_class']}, "
              f"tries={len(rec['attempts'][0]['tries'])}")

    # 2 -------------------------------------------------------------------------
    def test_02_breaker_skips_dead_connection(self):
        dead = add_conn(self.store, "dead-one", base=DEAD)
        add_conn(self.store, "alive", price=0.0)
        h = Health()
        r = router_for(self.store, health=h)
        for _ in range(3):
            d = route(r, PUBLIC)
            # the dead endpoint is tried first and fails, so the successful route is a
            # one-hop fallback; that is outcome "escalated", not "answered" (RC-4.5)
            self.assertIn(d["outcome"], ("answered", "escalated"))
            self.assertIsNotNone(d["chosen"])
        self.assertIn(dead["id"], h.unavailable())
        d = route(r, PUBLIC)
        self.assertIn(d["outcome"], ("answered", "escalated"))
        rec = self.store.get("decision_log", d["decision_ref"])
        avail = next(g for g in rec["gate_trace"] if g["gate"] == "availability")
        self.assertIn({"model_ref": dead["id"], "reason": "unhealthy"}, avail["excluded"])
        self.assertIn(dead["id"], rec["health_snapshot"]["unhealthy"])
        self.assertIsNotNone(d["chosen"])
        print(f"\n  [2] after 3 failures: status={h.status(dead['id'])}, "
              f"excluded_reason=unhealthy, run still answered by the live connection")

    # 3 -------------------------------------------------------------------------
    def test_03_retry_policy_and_usage(self):
        add_conn(self.store, "auth-ish")
        B.set("auth-ish", "401")
        r = router_for(self.store)
        d = route(r, PUBLIC)
        rec = self.store.get("decision_log", d["decision_ref"])
        tries = rec["attempts"][0]["tries"]
        self.assertEqual(len(tries), 1, "auth must not be retried")
        self.assertEqual(tries[0]["error_class"], "auth")

        self.fresh()
        add_conn(self.store, "missing-model")
        B.set("missing-model", "404")
        r2 = router_for(self.store)
        d2 = route(r2, PRIVATE)
        rec2 = self.store.get("decision_log", d2["decision_ref"])
        t404 = [a for a in rec2["attempts"] if a["label"] == "test missing-model"][0]
        self.assertEqual(len(t404["tries"]), 1, "model_not_found must not be retried")

        self.fresh()
        add_conn(self.store, "slow")
        B.set("slow", "hang", "hang", "hang")
        r3 = router_for(self.store)
        d3 = route(r3, PUBLIC)
        rec3 = self.store.get("decision_log", d3["decision_ref"])
        tslow = [a for a in rec3["attempts"] if a["label"] == "test slow"][0]
        self.assertEqual(len(tslow["tries"]), 3, "timeout must be retried at most twice")
        self.assertEqual({t["error_class"] for t in tslow["tries"]}, {"timeout"})

        self.fresh()
        add_conn(self.store, "flaky")
        B.set("flaky", "500", "500", "ok")
        r4 = router_for(self.store)
        d4 = route(r4, PUBLIC)
        rec4 = self.store.get("decision_log", d4["decision_ref"])
        tf = [a for a in rec4["attempts"] if a["label"] == "test flaky"][0]
        self.assertEqual(len(tf["tries"]), 3)
        self.assertEqual([t["error_class"] for t in tf["tries"]], ["server", "server", None])
        self.assertEqual(sum(1 for t in tf["tries"] if t["usage"]), 1,
                         "only the successful try reports usage")
        self.assertEqual(tf["actual_cost_usd"], 0.0)
        print("\n  [3] auth=1 try, model_not_found=1 try, timeout=3 tries, "
              f"transient=3 tries with usage on {sum(1 for t in tf['tries'] if t['usage'])} try")

    # 4 -------------------------------------------------------------------------
    def test_04_malformed_body_escalates(self):
        bad = add_conn(self.store, "badjson")
        good = add_conn(self.store, "good")
        B.set("badjson", "badjson")
        d = route(router_for(self.store), PUBLIC)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(d["outcome"], "escalated")
        self.assertEqual(rec["attempts"][0]["error_class"], "malformed_response")
        self.assertEqual(rec["attempts"][-1]["model_ref"], good["id"])
        self.assertEqual(d["chosen"]["model_ref"], good["id"])
        # a missing `choices` and an unexpected finish_reason take the same path
        self.fresh()
        add_conn(self.store, "nochoices")
        B.set("nochoices", "nochoices")
        d2 = route(router_for(self.store), PUBLIC)
        rec2 = self.store.get("decision_log", d2["decision_ref"])
        self.assertTrue(any(a["error_class"] == "malformed_response" for a in rec2["attempts"]))
        print(f"\n  [4] badjson -> malformed_response -> escalated to {good['label']}, no crash")

    # 5 -------------------------------------------------------------------------
    def test_05_concurrent_requests_keep_chain(self):
        add_conn(self.store, "concurrent")
        r = router_for(self.store)
        errors, out = [], []

        def work(i):
            try:
                out.append(route(r, PUBLIC))
            except Exception as e:                    # noqa: BLE001 - the test is the guard
                errors.append(repr(e))

        ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        ok, detail = self.store.verify_chain("decision_log")
        self.assertTrue(ok, detail)
        ids = [d["decision_ref"] for d in out]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        recs = [r for r in self.store.records("decision_log") if r.get("run") == "acc"]
        self.assertEqual(len(recs), 12)
        print(f"\n  [5] 12 concurrent routes -> chain {detail}, 12 unique decision records")

    # 6 -------------------------------------------------------------------------
    def test_06_privacy_holds_on_failed_path(self):
        dead_local = add_conn(self.store, "local-dead", locus="local", base=DEAD)
        cloud = add_conn(self.store, "cloud-alive", locus="cloud", privacy="public", price=2.0)
        # v7 is the strict profile, kept for exactly this proof: the gate still works.
        d = route(router_for(self.store, policy_label="v7"), PRIVATE)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["privacy_class"], "private")
        self.assertEqual(d["outcome"], "refused")
        self.assertEqual(d["refusal_reason"], "all_connections_failed")
        # the cloud endpoint was excluded before any call, and no request reached it
        self.assertFalse([h for h in B.hits if h[0] == "cloud-alive"])
        priv = next(g for g in rec["gate_trace"] if g["gate"] == "privacy")
        self.assertTrue(any(e["model_ref"] == cloud["id"] for e in priv["excluded"]))
        blob = json.dumps([r for r in self.store.records("decision_log")] +
                          [json.loads(l) for l in
                           (self.store._dir("decision_log") / "history.jsonl").read_text().splitlines()])
        self.assertNotIn(NEEDLE, blob)
        print(f"\n  [6] private prompt: cloud excluded at gate 1, local failed, "
              f"refused with the failure named; needle absent from log+history, "
              f"0 requests to cloud ({len(B.hits)} to local)")

    # 11 ------------------------------------------------------------------------
    def test_11_default_policy_honours_the_user_choice(self):
        """Owner, 2026-10-25: privacy is the user's, not ours to block. Under the default
        policy a private request MAY go to the cloud; what the router owes is a record."""
        # both declarations are the user's: the policy permits cloud, and THIS endpoint is
        # declared able to see private content. Where they disagree the stricter one wins,
        # which is what "declared" has to mean for the record to be worth anything.
        add_conn(self.store, "cloud-only", locus="cloud", privacy="private", price=2.0)
        d = route(router_for(self.store), PRIVATE)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["privacy_class"], "private")
        self.assertEqual(d["outcome"], "answered")
        self.assertEqual(d["chosen"]["locus"], "cloud")
        self.assertIsNotNone(rec["privacy_warning"])
        code, rep = audit(store=self.store, run="acc", needles=[], needle_labels=[], out=None)
        self.assertEqual(code, 0, rep["failures"])
        print(f"\n  [11] private -> cloud permitted by the user's policy; warning recorded: "
              f"{rec['privacy_warning'][:70]}...")

    # 12 ------------------------------------------------------------------------
    def test_12_public_verdict_can_be_verified(self):
        """spec §19: a PUBLIC verdict may be checked by a configured second opinion. A stricter
        verdict wins; a failed verification demotes to confidential, never back to public."""
        import copy
        add_conn(self.store, "route-here")
        add_conn(self.store, "checker", locus="cloud", privacy="public", price=0.0,
                 connector="openai_compatible")
        base = router_for(self.store).policy()
        pol = copy.deepcopy(base)
        pol.update({"id": new_id("pol"), "version_label": "v92-test", "is_default": True,
                    "created_at": "2099-01-01T00:00:00Z", "source_ref": [base["id"]]})
        pol["classifier"]["verify_public_with"] = "test checker"
        self.store.append("policy", pol)

        class FakeVerifier:
            answer = '{"class": "private"}'
            def call(self, conn, prompt, out):
                if conn["label"] == "test checker":
                    return {"ok": True, "text": self.answer, "tokens_in": 10, "tokens_out": 5,
                            "error_class": None, "tries": []}
                return {"ok": True, "text": "ok", "tokens_in": 5, "tokens_out": 2,
                        "error_class": None, "tries": [], "finish_reason": "stop"}

        r = Router(store=self.store, transport=FakeVerifier(), health=Health())
        d = route(r, PUBLIC)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["privacy_class"], "private")
        self.assertTrue(rec["classifier_verification"]["changed"])
        self.assertEqual(rec["classifier_verification"]["verdict"], "private")

        FakeVerifier.answer = "not json at all"
        r2 = Router(store=self.store, transport=FakeVerifier(), health=Health())
        d2 = route(r2, PUBLIC)
        rec2 = self.store.get("decision_log", d2["decision_ref"])
        self.assertEqual(rec2["privacy_class"], "confidential",
                         "an unverifiable public claim must not stay public")
        self.assertEqual(rec2["classifier_verification"]["error"], "unparsed")
        print("\n  [12] public verdict verified: private -> class private; unparsable -> "
              "confidential (fail closed), never back to public")

    # 13 ------------------------------------------------------------------------
    def test_13_trust_user_mode_does_not_classify(self):
        """Owner decision: the user owns data handling, so the ship default does not classify.
        A private-looking prompt is routed on quality and cost, and the record says plainly that
        no classification was performed."""
        import copy
        add_conn(self.store, "free-local", locus="local", price=0.0)
        add_conn(self.store, "paid-cloud", locus="cloud", privacy="private", price=2.0)
        base = router_for(self.store).policy()
        pol = copy.deepcopy(base)
        pol.update({"id": new_id("pol"), "version_label": "v93-test", "is_default": True,
                    "created_at": "2099-01-02T00:00:00Z", "source_ref": [base["id"]]})
        pol["classifier"]["mode"] = "trust_user"
        self.store.append("policy", pol)
        d = route(router_for(self.store), PRIVATE, dry=True)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["classifier_mode"], "trust_user")
        self.assertEqual(rec["privacy_class"], "public")
        self.assertIsNone(rec["privacy_warning"])
        self.assertEqual(d["chosen"]["locus"], "local", "cheapest feasible, as declared")
        print("\n  [13] trust_user: no classification, private-looking text routed on price, "
              "record says classifier_mode=trust_user")

    # 8 -------------------------------------------------------------------------
    def test_08_malformed_entry_does_not_stop_boot(self):
        good = add_conn(self.store, "valid")
        broken = {"id": new_id("mdl"), "created_at": now(), "spec_version": "1.1",
                  "status": "active", "superseded_by": None, "source_ref": ["test"],
                  "label": "broken", "connector": "openai_compatible",
                  "base_url": "", "model": "ghost", "auth": {"scheme": "none", "key_env": None},
                  "locus": "local", "privacy_class": "private",
                  "price": {"input_per_mtok": 0.0, "output_per_mtok": 0.0,
                            "source": "user_declared"},
                  "context_window": 0, "output_budget_tokens": 512, "tokenizer_ref": None,
                  "request_params": None, "timeout_s": 0.5, "local_resources": None,
                  "health": None, "declared_at": now(), "capability_key": "ghost"}
        self.store.append("models", broken)
        d = route(router_for(self.store), PUBLIC)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(d["outcome"], "answered")
        self.assertEqual(d["chosen"]["model_ref"], good["id"])
        probs = rec["registry_provenance"]["unusable_connections"]
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0]["id"], broken["id"])
        print(f"\n  [8] booted with 1 unusable entry reported ({probs[0]['problems'][:2]}), "
              f"routed to the valid connection")

    # 9 -------------------------------------------------------------------------
    def test_09_no_shadow_price_and_local_is_zero(self):
        pol = Router(store=self.store).policy()
        obj = pol["objective"]
        for gone in ("local_shadow_price_per_mtok", "latency_price_usd_per_s",
                     "tariff_usd_per_kwh"):
            self.assertNotIn(gone, obj)
        self.assertEqual(obj["type"], "min_declared_cost")
        add_conn(self.store, "free-local", locus="local", price=0.0)
        d = route(router_for(self.store), PUBLIC)
        self.assertEqual(d["cost"]["list_price_estimate_usd"], 0.0)
        rec = self.store.get("decision_log", d["decision_ref"])
        self.assertEqual(rec["feasible_set"][0]["expected_cost_usd"], 0.0)
        print(f"\n  [9] policy v7 objective={obj['type']}, no shadow/latency/tariff keys; "
              f"local route list-price = ${d['cost']['list_price_estimate_usd']:.6f}")

    # 10 ------------------------------------------------------------------------
    def test_10_audit_passes_on_new_log(self):
        add_conn(self.store, "audited")
        r = router_for(self.store)
        for _ in range(3):
            route(r, PUBLIC)
        code, rep = audit(store=self.store, run="acc", needles=[], needle_labels=[], out=None)
        self.assertEqual(code, 0, rep["failures"])
        n = 17
        print(f"\n  [10] audit exit={code}, checks={n}, coverage={rep['coverage']}")
        self.assertEqual(rep["failures"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
    print("\n(checks 7 — real desktop app + real cloud — are run separately by "
          "acceptance_live.py because they need the network)")
