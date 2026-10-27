# required_changes.md — binding change instructions for the Layer 5 agent

**From:** owner (via monitor) · **Date:** 2026-10-23 · **Status:** binding, supersedes the
named sections below
**Applies to:** `05-model-router/` — `brief.md`, `spec.md`, `router/`, `datasets/`

This is not a suggestion list. Items marked **MUST** are required before the layer is
considered conformant again. Where this file contradicts `spec.md` or `brief.md`, this
file wins; record the change in the spec as a numbered delta and keep the old text
visible rather than silently rewriting it.

---

## 0. The owner's decision in one paragraph

The router does **not** decide which models exist. **The user connects the models they
want and that list is the entire world the router may choose from.** Connecting is the
user's responsibility and their risk; routing well within that list is the router's job.
Because nearly every local runtime, API provider, and desktop LLM app already speaks the
same OpenAI-compatible HTTP protocol, one generic connector covers all of them — so this
change **deletes code**, it does not add any. Local models are genuinely free, so the
invented "shadow price" is removed from the shipped policy. The cost–quality frontier
stays as the paper's comparison, not as the product's decision rule.

---

## 1. The registry is the user's connect list

**RC-1.1 — MUST.** `ds:models` stops being an agent-curated catalog and becomes a
user-authored connection list. Each record describes **one connection to one model**:

```jsonc
{
  "id": "mdl_...",
  "label": "My LM Studio Qwen",        // user-facing name, free text
  "connector": "openai_compatible",     // openai_compatible | ollama
  "base_url": "http://127.0.0.1:1234/v1",
  "model": "qwen2.5-7b-instruct",       // the id the endpoint expects
  "auth": { "scheme": "bearer", "key_env": "LMSTUDIO_API_KEY" },  // scheme: bearer | header | none
  "locus": "local",                     // local | cloud  — declared by the user
  "privacy_class": "P0",                // what this endpoint is allowed to see
  "price": { "input_per_mtok": 0.0, "output_per_mtok": 0.0, "source": "user_declared" },
  "context_window": 32768,
  "declared_at": "…", "spec_version": "1.1"
}
```

**RC-1.2 — MUST.** Secrets never enter a dataset record. Records carry the **name** of an
environment variable (`key_env`), never a key value. A missing variable is a startup
error naming the variable, not a silent `None`.

**RC-1.3 — MUST.** `locus` and `privacy_class` are **user declarations**, and the privacy
gate trusts them for routing while the log marks them as declared, not measured. A user
who declares a cloud endpoint as `local` defeats the privacy gate; that is their choice,
it is stated in the paper's honesty section, and it must not be silently "corrected" by
guessing from the URL. (Optional, cheap improvement: warn in the log when `base_url`
resolves to a non-loopback address but `locus` says `local`.)

**RC-1.4 — MUST.** `price` is required for every connection, may be 0, and is
`user_declared`. If the user leaves it blank, the record is admitted with
`price_source: "unknown"` and the router **MUST NOT** invent a price — it treats the
connection as unrankable by cost and says so in the decision record.

**RC-1.5 — MUST.** No runtime catalog fetching, provider enumeration, or "suggest a
model" behaviour. `fetch_catalog.py` and `probe_cloud.py` are demoted to experiment
tooling under `experiments/`, or deleted; they must not be imported by `router/decide.py`
or reachable from the product path.

**RC-1.6 — SHOULD.** Provide a `connections.example.jsonl` in the layer folder showing
one entry per connector type (a local Ollama model, a local OpenAI-compatible desktop
app, and one cloud provider), so a user can copy-paste to start.

---

## 2. One generic connector, plus Ollama

**RC-2.1 — MUST.** Implement exactly two backend classes:

- `openai_compatible` — POST `{base_url}/chat/completions`, bearer/header/none auth,
  `usage` parsed when present. This single class covers Together, DashScope, OpenRouter,
  Groq, LM Studio, Jan, GPT4All, Msty, llama.cpp's server, vLLM, and anything else
  speaking the same protocol.
- `ollama` — the native `/api/chat` path (kept because its API and token accounting
  differ).

**RC-2.2 — MUST.** Delete `router/backends/together.py` and
`router/backends/dashscope.py` once their behaviour is covered by the generic class.
Their quirks move into **data** (`request_params` on the connection record), not code.
No per-provider `if` branches anywhere in the routing path.

**RC-2.3 — MUST.** Connection-specific quirks that cannot be expressed in data are
recorded as a named limitation in the spec, not patched with a special case.

**RC-2.4 — MUST.** The connector interface stays the same shape for both:
`complete(request) -> {text, usage, finish_reason, latency_ms, error}`. `error` is a
typed value (`auth`, `rate_limit`, `timeout`, `connection`, `model_not_found`,
`bad_request`, `server`, `malformed_response`), never a bare string or an exception that
escapes `route()`.

---

## 3. Cost: local is free, and the shadow price goes

**RC-3.1 — MUST.** Delete the local shadow-price and latency-price policy knobs
(`spec.md` §3.2, §12.6). A local connection costs what the user declared, which is $0 by
default. The router does not manufacture a valuation on the user's behalf.

**RC-3.2 — MUST.** Replace the objective with the simple rule it was standing in for:
among feasible connections, prefer the lowest declared cost; tie-break deterministically
by `id_asc`. Local connections therefore win by default whenever they are feasible —
which is the intended product behaviour, not a bug to be fixed with a synthetic price.

**RC-3.3 — MUST.** Escalate past a local connection only on a **machine-checkable**
signal: no eligible local connection, context/machine fit failure, or a failed call
(`timeout`, `connection`, `server`, `malformed_response`). Self-reported confidence stays
a secondary, optional trigger and never outranks these.

**RC-3.4 — MUST.** The cost–quality frontier remains as **experiment 1's comparison**
(always-small / always-big / routed) with local priced at $0. Any crossover analysis is a
**sensitivity curve in the paper**, clearly labelled a user valuation, and is not a
policy input. Figures must not imply local has a measured price.

**RC-3.5 — MUST.** Cost is reported as two numbers — **actual spend** from provider
meters and **list-price estimate** — and never conflated.

---

## 4. Resilience — required behaviour under failure

The layer must remain usable and auditable when any part of the outside world misbehaves.
Resilience here means: **a broken connection degrades the answer, never the router.**

**RC-4.1 — MUST. Per-call timeout.** Every network call has a declared timeout
(prompt-phase and total). A local app that hangs must not hang the OS. A timed-out call
returns a typed `timeout` and does not block the event loop for subsequent requests.

**RC-4.2 — MUST. Bounded retries.** At most 2 retries, exponential backoff with jitter,
and **only** for transient classes (`timeout`, `rate_limit`, `server`, `connection`).
Never retry `auth`, `bad_request`, or `model_not_found` — those cannot succeed by
repetition. Every attempt's usage is recorded so a retried call cannot silently double-count
cost.

**RC-4.3 — MUST. Circuit breaker per connection.** After N consecutive transient failures
(declare N; 3 is reasonable), the connection is marked unhealthy for a cooldown period
(declare it; 60 s is reasonable). While unhealthy it is excluded from the feasible set
and the decision record says `excluded_reason: "unhealthy"`. It is re-admitted
automatically after a successful health check. This is what keeps a dead desktop app from
costing every request a timeout.

**RC-4.4 — MUST. Availability is measured, never assumed.** A connection is `available`
only after a real call succeeds. An unavailable connection is retired from routing and
the retirement is a chained record. Re-check cheaply on a schedule or on first use after
cooldown — not on every request.

**RC-4.5 — MUST. Fallback within the feasible set.** If the chosen connection fails at
call time, the router tries the next feasible one and records the **attempt chain** in the
decision record (which connections were tried, in order, with which error). The result is
still **one** decision record, not several.

**RC-4.6 — MUST. Never lose the record.** A failed, refused, or degraded request still
produces exactly one bound decision record, written even when the store is mid-failure
(append, then verify chain). Logging failure must not be able to suppress the log entry
that describes it.

**RC-4.7 — MUST. Concurrency safety.** Parallel requests must not corrupt the chained
history. Appends are atomic and serialised (file lock or equivalent); a concurrent second
writer waits, it does not drop or interleave a line. Include a test that fires concurrent
requests and verifies the chain.

**RC-4.8 — MUST. Privacy holds under failure.** No prompt content may be sent to a
privacy-forbidden endpoint, including on retries, fallbacks, health checks, and error
paths. Health checks use a fixed public probe payload — never the user's prompt. A
proof-of-absence scan must run over the failed-attempt path too, not only the success path.

**RC-4.9 — MUST. Malformed responses are typed failures.** A non-JSON body, a missing
`choices`, or an unexpected `finish_reason` becomes `malformed_response` and escalates; it
must not raise an unhandled exception or be scored as a wrong answer.

**RC-4.10 — MUST. Degradation is visible.** If every feasible connection fails, the
outcome is a typed **refusal with reason** (not an exception, not an empty string), and
the response carries which connections failed and why. The shell will show this.

**RC-4.11 — MUST. Startup tolerates a partial world.** If one connection in the list is
malformed, missing its key, or unreachable, the router starts with the rest and reports
the broken entries. One bad line in the user's list must not prevent the OS from booting.

**RC-4.12 — SHOULD. Streaming is optional and off by default.** The non-streaming path is
the contract. If streaming is added, a mid-stream break is a typed failure and the partial
text is not presented as an answer.

---

## 5. Desktop apps — the honest boundary

**RC-5.1 — MUST.** Any desktop app that exposes an HTTP endpoint the user can point at is
covered by the `openai_compatible` connector, with no app-specific code. The layer must
ship a tested example against at least one real desktop app (LM Studio, Jan, GPT4All, or
llama.cpp server) and record which one and which version.

**RC-5.2 — MUST.** Apps that expose **no** endpoint (GUI only) are out of scope — for this
layer **and** for the shell. Do not build screen automation, AppleScript, accessibility-API
bridges, deep links, or any "open the app with the prompt pasted" convenience. The owner's
position (2026-10-23): the user installs such apps and uses them however they like; the OS
neither drives them nor pretends to. State this plainly in the spec and the paper.

**RC-5.3 — SHOULD.** Document the one-time setup per common app that *does* expose a local
endpoint (start its server, note host/port, paste into `connections.jsonl`) in a short
`CONNECT.md`. Documentation, not code.

**RC-5.4 — MUST.** The spec and paper must state the commercial consequence explicitly:
**a subscription to a closed desktop app is not an API entitlement.** A ChatGPT Plus or
Kimi subscription cannot be routed through; the user who wants that model routed must
connect its API with a separately billed key. Name the mapping — ChatGPT desktop → OpenAI
API, Kimi Work → Moonshot/Kimi platform API (OpenAI-compatible, so the generic connector
already covers it) — so the expectation is set before the user is disappointed.

**RC-5.5 — MUST.** Three connection kinds, and only these three, are routable:

| Kind | Example | Connector |
|---|---|---|
| Local runtime with an endpoint | Ollama, LM Studio, Jan, GPT4All, llama.cpp server, vLLM | `openai_compatible` (Ollama: native) |
| API provider | OpenAI, Moonshot/Kimi, Together, DashScope, OpenRouter, Groq | `openai_compatible` |
| Closed GUI app | ChatGPT desktop, Kimi Work | **none — not a routing target** |

---

## 6. What must NOT change

These were bought and paid for; do not regress them.

- The privacy gate stays a hard set-filter, decided before any network call.
- Refusal stays a typed, logged outcome.
- Every decision stays a bound record, replayable by `router/audit.py` against the registry
  **as it stood at decision time**.
- Capability rows come only from frozen runs; unmeasured pairs stay `allow_flagged` and are
  counted in coverage.
- Qafiya §8 storage layout and chained history for all datasets; prefixed sortable ids.
- Experiment 2's result and battery are untouched.
- No auto-teardown, no auto-stop on cost; caps alert, humans decide.

---

## 7. Acceptance checks (the agent must show these passing)

1. `route()` returns a typed refusal — no exception — when the only connection's endpoint
   is down, and the decision record names the failed connection and the error class.
2. A dead connection trips the breaker and is skipped on the next request within the
   cooldown, with `excluded_reason: "unhealthy"` in the record.
3. `auth` and `model_not_found` errors are **not** retried; `timeout` is retried at most
   twice; attempt usage is recorded.
4. A malformed JSON body escalates instead of crashing.
5. Concurrent requests leave a valid hash chain (test included).
6. Privacy proof-of-absence passes over a run that includes failed attempts and fallbacks.
7. The same `openai_compatible` class answers from (a) a real desktop app and (b) one real
   cloud provider, with zero provider-specific code in the routing path.
8. A list containing one malformed entry still boots, routing the valid entries.
9. No shadow-price or latency-price field exists in `ds:policy`; exp1 still produces its
   frontier numbers with local at $0.
10. `router/audit.py` still passes 17/17 on a log produced under the new code.

---

## 8. Deliverables, in order

1. Spec delta — one numbered section recording RC-1…RC-5 as changes, with the old text
   preserved.
2. Code changes, with `together.py`/`dashscope.py` deleted and no per-provider branching.
3. The ten acceptance checks above, as tests with their output captured.
4. `CONNECT.md` + `connections.example.jsonl`.
5. Re-run the spine; headline numbers must recompute or the change is not done.
6. Report back: what was deleted (lines), what remains, and anything in this file that
   turned out to be wrong or impossible. Push-back with evidence is welcome; silence is not.

---

## 9. Out of scope

Discovery or suggestion of models, provider catalogs at runtime, GUI-only apps, streaming
by default, multi-provider load balancing, batching, quantization, and any change to
Layers 1–4 or the root registry (monitor-owned).
