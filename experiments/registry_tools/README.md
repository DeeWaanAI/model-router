# registry_tools — pre-RC tools, kept for provenance

These scripts produced the registry artifacts cited in `spec.md` §13.3 and the frozen
experiment rolls. They are **not** on the product path and are not maintained after RC-1/RC-2
(2026-10-25):

* `fetch_catalog.py`, `probe_cloud.py`, `probe_local.py`, `register_models.py` — catalog and
  availability probing. The router no longer fetches catalogs or suggests models; the user
  connects what they want (`CONNECT.md`). Archived, not maintained: `probe_cloud.py` still
  imports the deleted `backends/together.py`, so it will not run as-is.
* `superseded/` — scripts written against the deleted per-provider classes
  (`together.py`, `dashscope.py`, `ollama_backend.py`, `http.py`) and the pre-RC registry
  shape. Kept so the evidence trail is reproducible in principle; they will not run against
  the current `ds:models` schema without a rewrite. `test_router_pre_rc.py` is the pre-RC
  router test suite, superseded by `../../router/test_acceptance_rc.py`.
