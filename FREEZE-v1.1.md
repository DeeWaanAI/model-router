# FREEZE — Layer 5 spec v1.1 · 2026-10-26

**Frozen artifact:** `spec.md` — Layer 5 (Model Router) formal specification, version 1.1.
**sha256:** `e009407e6becfcbdfee502d8dca56dd506027be0bf785bcb3c685d7eaa03f98d` (v1.1 + §16.5 resolution note, same day)
**Authority:** `instructions_26_oct_2026.md` (owner). Decision-log entry: spec §21.

## What is frozen

The simplified (RC-1…5) system:

- `ds:models` is the user's connect list; secrets are env-var names only (RC-1).
- Exactly two wire formats: `openai_compatible` and `ollama`; no per-provider branches (RC-2).
- No shadow price / latency price; objective `min_declared_cost`; local wins by being declared $0 (RC-3).
- The RC-4 failure model: timeouts, bounded retries, per-connection circuit breaker, measured
  availability, fallback inside the feasible set, file-locked appends, typed refusal.
- Three connection kinds; GUI-only apps are not routing targets (RC-5).
- Classifier default `trust_user` (§20); `rules` profile opt-in.

## Freeze-state verification (all recomputed 2026-10-26, this machine)

| Check | Result |
|---|---|
| `router/test_store.py` | OK |
| `router/test_acceptance_rc.py` (offline legs) | exit 0, 12 checks |
| `router/acceptance_live.py` (check 7, live) | PASS — one `openai_compatible` class served Ollama `/v1` (llama3.2, 51 in / 6 out, $0) and Together (38 in / 348 out, metered $0.0001797); DashScope leg a typed refusal (invalid key, known) |
| desktop-app leg (check 7, RC-5.1) | PASS — `experiments/desktop_app_check/check7_lmstudio.py`: the same generic connector routed through **LM Studio's bundled llama.cpp server** (2.54.0, Apple Metal arm64) serving Qwen2.5-0.5B-GGUF at `127.0.0.1:1234/v1` — answered, provider-metered (55 in / 93 out), $0. Evidence: `experiments/desktop_app_check/evidence.json`. Caveat recorded: the LM Studio GUI itself would not launch headless (first-run dialogs need a human); its own bundled backend binary was run directly |
| `router/audit.py` | 17/17, 828 decisions, binding coverage 1.0 |
| `experiments/spine.py` | exit 0 — all exp1/exp2/exp3 headline numbers recomputed from frozen artifacts |

## Registry prefixes (for the monitor)

Adopted and in use: `mdl_*`, `cap_*`, `pol_*`, `rul_*`, `dec_*`, `req_*`.
No dataset was dropped in the simplification — **no prefix is retired**.

## Note for INTERFACES.md

Layer 5 spec v1.1 is the contract Layer 6 codes against. The monitor is asked to register the
six prefixes above and mark this spec frozen at v1.1.
