# FREEZE — Experiment 1: Cost–Quality Frontier (pre-registration)

**Frozen:** 2026-10-22 · **Reps:** 1 (pilot) · **Temperature:** 0 · **Pilot cost cap:** $10 (owner), expected ≈ $0.6

## 1. Frozen battery

`battery/items.jsonl` — 6 items, sha256 recorded in `battery/manifest.json`. The corpus is
**copied** into the battery and checksummed, so the experiment does not depend on another
layer's folder staying put.

**Ground truth is computed by `make_battery.py` from the CSVs — no LLM judge, no human
annotation.** That is what makes the quality number re-computable by the spine.

| Item | Class | Input chars | Expected (computed) |
|---|---|---|---|
| `b_total_small` (50 rows) | tabular_summary | 2,066 | `n_rows=50, total=25508.69` |
| `b_max_medium` (200 rows) | tabular_summary | 7,727 | `max_amount=893.67, max_source=r_0042` |
| `b_groupby_medium` | tabular_summary | 7,721 | per-category sums, 4 keys |
| `b_total_large` (800 rows) | tabular_summary | 30,339 | `n_rows=800, total=361572.4` |
| `b_top_vendor_large` | tabular_summary | 30,354 | `top_vendor=Stark, total=57194.72` |
| `b_selective_travel` (4 files) | multi_doc_selective | 21,491 | per-file travel totals + overall |

Every prompt demands **one valid JSON object and nothing else**, so the three quantities
`ds:capabilities` needs fall out of the same run: `accuracy` (fields matched),
`schema_compliance` (parses as a single JSON value), `termination_rate` (finished before the
token budget).

`b_total_large` is deliberately the case the upstream live runs measured at **0/12**
(`efficiency-experiment/FREEZE.md` H4). The hazard is reproduced, not avoided.

## 2. Arms

| Arm | Variant(s) | Price (in/out per Mtok) | Role |
|---|---|---|---|
| always-small | `llama3.2:latest` 3B Q4_K_M | $0 | the floor |
| always-small-plus | `llama3.1:8b-instruct-q4_K_M` | $0 | the middle point the owner approved |
| always-big | `Llama-3.3-70B-Instruct-Turbo` | $1.04 / $1.04 | the quality ceiling that is affordable |
| always-big-max | `Qwen3.8-2.4T-A95B` (chat) | $2.00 / $6.00 | the most expensive callable arm |
| hazard | `Qwen3.8-2.4T-A95B` (reasoning, 8192 budget) | $2.00 / $6.00 | 2 items only — reproduces the non-termination hazard |
| **routed** | policy v5 decides | — | the arm this layer contributes |

## 3. Method, and why forced arms are not logged as decisions

The **routed** arm goes through `route()` and is written to `ds:decision_log` as bound
records — it is a routing decision, and it must be auditable.
The **forced** arms are experiment controls: they deliberately ignore the gates, so writing
them into the decision log would put non-decisions into the evidence base and corrupt M1/M2
of experiment 2. They are recorded in `results/raw/` instead, with the same metered fields.
This is a scope decision, logged here before the run rather than discovered afterwards.

Costs are the provider's own metered token counts times the frozen registry price — never an
estimate. Estimate-vs-actual error is reported (spec §10.4).

## 4. Metrics and hypotheses

| # | Metric |
|---|---|
| F1 | measured USD per arm, and per correct answer (cost of quality) |
| F2 | quality per arm = mean fraction of expected fields matched exactly |
| F3 | the routed arm's position relative to the two always-X arms |
| F4 | crossover: where routing stops paying, reported as a **sensitivity curve over a declared local-price valuation** — a paper analysis labelled as the user's valuation, never a policy input (RC-3.4) |
| F5 | termination and schema compliance per arm (feeds `ds:capabilities`, experiment 3) |

- **H1** Routing costs far less than always-big at comparable quality, because privacy-gated
  and short items go local or to the cheap arm.
- **H2** always-small loses quality specifically on the 800-row items, and the loss is
  arithmetic, not formatting.
- **H3** The upstream 0/12 result reproduces: at least the two large-row items are failed by
  **every** arm. If so, the honest conclusion is the upstream one — totals must come from
  bindings, not from the model — and the frontier for that class is degenerate. Reported as
  a result either way.
- **H4** The reasoning arm hits its token budget on the large tabular items, i.e.
  `finish_reason: length` again. That is the verifiability hazard of experiment 3, measured
  here as a side effect of the same run.

## 5. Stop condition

Pilot only: 1 rep, 6 items, ~5 arms. Pre-flight estimate is printed from the frozen prices
before any call; if it exceeds $1.50 the run halts before spending and the figure is
reported. No full multi-rep run happens until the pilot's actual cost is reported.

## 6. Amendment B.1 (2026-10-23, after the frozen roll) — instrument defect in `expected`

Discovered while wiring the spine, not while looking for it. Four items — `b_total_small_code`,
`b_total_small_inhead`, `b_total_large_code`, `b_total_large_inhead` — tell the model to print
`{"n_rows": <int>, "total_amount": <number>}` and store the ground truth as
`{"n_rows": …, "total": …}`. The scorer matches fields **by name**, so a program that ran and
printed the exactly correct sum lost the `total` field. The prompt was never wrong and was never
changed; only the ground-truth key was mislabelled, so every model answered the same question in
both the pre-registered and the corrected reading.

Detection was mechanical, not hand-picked: `experiments/exp1/regrade.py` parses the shape each
prompt mandates (placeholders replaced by `null`, then real JSON), diffs it against `expected` for
all 12 items, and reports the diff set. It found exactly these 4 items and no others; the nested
`b_travel_across_files_*` shapes parse clean. Ground truth was re-summed from the frozen corpus
independently: 800 rows / 361,572.40 and 50 rows / 25,508.69.

**Correction method: regrade, not re-roll.** The raw outputs are already on disk, so the alias
`total_amount → total` re-interprets existing evidence at $0 and no new spend. A fresh roll would
have bought sampling noise around a scoring bug. `results/raw/arms_exp1-pilot-v3.jsonl` is
untouched; the corrected rows are the sidecar `results/derived/arms_exp1-pilot-v3-regraded.jsonl`,
each carrying a `grader: {version: "B.1-key-alias"}` field.

| model | compute_via_code accuracy, frozen grader → B.1 grader |
|---|---|
| llama3.2:latest (3B) | 0.0 → 0.0 |
| qwen-flash | 0.3333 → 0.5 |
| llama3.1:8b (local) | 0.5 → 0.6667 |
| Llama-3.3-70B | 0.6667 → 0.8333 |
| Qwen3.8-2.4T-A95B | 0.8333 → **1.0** |

**What this changes about H2/H3.** H3 predicted the large-row items are failed by every arm, which
was the upstream conclusion (0/12). Corrected: on the 800-row total, **four of five arms print the
exactly right answer when they write and run a program** — including the local 8B and the $0.05/Mtok
cloud arm — while **zero** arms get it right in context. The hypothesis holds for the in-head arm and
is refuted for the compute-via-code arm, and that refutation is the layer's central architectural
claim. It had been hidden by my own grader.

**Limits of this amendment, stated.**
1. The two `*_inhead` items on the mismatch list cannot be regraded: `run_v2.py` stored the in-head
   *score* but not the in-head *reply text*, so there is nothing on disk to re-score. Those 30 rows
   carry their original numbers and are labelled `not_regradable_no_reply_text`. The fix
   (preserve reply text in `results/raw`) is in `run_v2.py` now; it matters because a grade that
   cannot be recomputed from the artifact is not auditable.
2. `schema_compliance` for code items means "the program printed a parseable JSON object" — it does
   **not** check key names, so the B.1 defect never showed up there. Under this definition the
   shipped `min_schema_compliance: 0.9` is nearly free for any arm whose program runs at all; the
   eligibility sweep says so explicitly.
3. Numbers in `ds:capabilities` written before this amendment used the frozen grader. They are
   superseded, not deleted (spec §9.1), and the replacing rows name the B.1 grader.
