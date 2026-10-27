# Layer 5 — Model Router · Formal Specification

**Version:** 1.1 — **frozen 2026-10-26**, per `instructions_26_oct_2026.md`. The RC-1…5 simplification (user-owned connect list, one generic OpenAI-compatible connector, no shadow price, secrets as env-var names) is complete and is the normative text; superseded pre-RC passages are marked as such and kept only as the record of what was believed then (§12, §14).
**Product:** DeeWaanAI OS, Layer 5 (Model Router)
**Builds on:** Qafiya spec v1.1 (frozen) + v1.3 hash-chained history (frozen, qafiya-core v0.2.0). Every dataset below IS a Qafiya dataset and obeys its storage layout (§8), binding primitive (§2), two-layer check (§3), invalidation rules (§6) and invariants (§7). This spec adds only what routing requires.
**Normative language:** MUST / SHOULD / MAY as in RFC 2119.
**Paper:** *Where Should This Query Run? Cost-, Privacy-, and Verifiability-Aware Model Routing for Personal AI Systems*
**Confirms:** `brief.md` v0.2, owner-confirmed 2026-10-22, items (a)–(d). Where this spec **strengthens or reinterprets** the brief, the change is listed in §12 and flagged for owner decision — nothing is silently altered.

**Owner confirmation of this spec (2026-10-22), item by item:**

| Item | Decision |
|---|---|
| §12.2 gate 3 covers machine fit | **confirmed** — a model that cannot fit in RAM becomes a logged refusal, not a runtime failure |
| §12.4 response text + HMAC prompt hash | **confirmed** — the no-text rule covers responses too; unsalted hashes replaced by HMAC under a local `.env` key |
| §12.5 `eligible` as a derived field | **confirmed** — threshold ablation is a policy edit, not a data rewrite |
| ~~§12.6 shadow price + latency price as policy knobs~~ | **superseded 2026-10-23 by RC-3** — both knobs are removed from the shipped policy; see §12.6 and §14 |
| §12.7 unmeasured pairs default to `allow_flagged` | **confirmed** — experiment 3 runs both modes and reports the difference |
| §12.8 registry contents | **confirmed** — 3 cloud variants spanning $0.02–$3.50/Mtok; local arms 3B–8B Q4 |
| §12.1, §12.3 monitor items | **referred to the monitor.** Not blocking: this spec's chosen forms (`mdl_*`/`cap_*`/`pol_*`/`rul_*`/`dec_*`/`req_*` ids, `min` not `argmin`, `{dataset, record}` targets) are used as-is and a rename is mechanical, touching no claim |

Two further owner decisions recorded the same day:

- **A second local arm (8B) is approved** to give a middle point on the cost–quality curve. Status: `llama3.1:8b` pull **blocked by a network fault, not by cost** — the local resolver (192.168.100.1) returns REFUSED for `registry.ollama.ai`, while public resolvers (1.1.1.1 / 8.8.8.8 / 9.9.9.9) all answer `104.18.16.170`, and HTTPS to that address succeeds in 0.4 s. Fix requires a system-level DNS or hosts change, which needs the owner's go. Work proceeds on the installed 3B arm meanwhile.
- **Autonomy granted:** proceed through prototype and experiments without per-step approval. Binding limits retained: pilot spend capped at **$10**, actual cost reported after the pilot and before the full run; no force-push, no branch deletion, no deployment, no system-level config change without asking.

## 0. Thesis

> *A routing decision is not a prediction — it is a proof obligation over frozen data: which models were allowed, by which rule, at which price, and the answer is replayable.*

Existing routers optimise one soft currency (cost, subject to quality) over a model zoo treated as homogeneous [litreview §3.1–3.2]. This layer makes routing an **explicit policy over three independent currencies** — privacy (hard gate), verifiability (eligibility constraint from frozen measurement), cost (soft objective, minimised only inside the feasible set) — where the policy is data, and every decision is a provenance-carrying record that an auditor can replay without trusting the router.

The consequence for design: **the router's job is to produce an auditable decision, not a good guess.** Determinism, replayability and refusal are first-class outputs. Accuracy-per-dollar is measured, but it is not the contribution.

## 1. Datasets

Five datasets under `datasets/`, each with the full Qafiya §8 fileset: `schema.json`, `data.jsonl`, `history.jsonl` (chained per v1.3), `bindings.jsonl`, `SUPERSEDED.json`.

Rationale for five rather than the brief's three: the brief's `ds:policy.task_classifier.rules_ref` already points at a rules record, and the owner's binding caveat is that **every tunable lives in data, never in code**. The classifier rule tables are tunables, so they are a dataset (`ds:rules`), versioned independently of the policy that selects them. The decision log is a dataset because it is the evidence for every headline number.

### 1.1 Base fields (all five datasets)

| Field | Type | Rule |
|---|---|---|
| `id` | id | immutable, prefixed sortable (ULID payload). Never bare `uuid4` (PROJECT.md §identity). Prefixes proposed in §12.1 |
| `created_at` | timestamp | set once, never edited |
| `spec_version` | string | `"1.1"` — the Qafiya data-model version this record conforms to |
| `status` | enum[active, retired, discredited] | read-path filter (§9) |
| `superseded_by` | id \| null | successor record; null while active |
| `source_ref` | list<id> | provenance: what this record was derived from; non-empty except for hand-declared registry entries, which carry `provenance` instead (§1.2) |

### 1.2 `ds:models` — the model registry

**One record per model *variant*, not per model family.** A variant is `(backend, upstream model id, mode, output budget)`. This is forced by evidence, not taste: the Qafiya live runs measured the *same* models **failing to terminate** on every large tabular prompt in thinking mode, and answering immediately with reasoning disabled (`02-view-renderer/qafiya-ref/experiments/efficiency-experiment/FREEZE.md`, note D5: 24/24 calls consumed the whole completion budget without emitting an answer, `finish_reason: length`, budgets 8k then 24k). A family-level record cannot express that.

| Field | Type | Rule |
|---|---|---|
| `name` | string | human label, e.g. `llama3.2:latest` |
| `backend` | enum[ollama, together] | extensible by schema version bump |
| `upstream_id` | string | provider's own identifier (`meta-llama/Llama-3.3-70B-Instruct-Turbo`) |
| `locus` | enum[local, cloud] | where inference runs. Derived from `backend` at registration; stored so gate 1 never depends on provider metadata |
| `mode` | enum[chat, reasoning] | reasoning mode is a separate record |
| `reasoning_enabled` | boolean | the request flag actually sent (`reasoning: {enabled: false}` per FREEZE D5) |
| `output_budget_tokens` | number | the completion cap this variant is run under. Capability rows are scoped to it (§1.3) |
| `clearance` | enum[public, confidential, private] | the **most sensitive** request class this variant may serve. Ordering: `public < confidential < private` |
| `price` | object | `{ input_per_mtok, output_per_mtok, unit: "usd_per_million_tokens", tier }` — see §3.1 for the unit trap and for tiered pricing |
| `context_window` | number | tokens, input + output |
| `tokenizer_ref` | string | the tokenizer used to estimate this variant's tokens. **Per-variant, never shared** (§3.3) |
| `tokenizer_agreement` | object \| null | measured estimate-vs-meter error for this variant: `{ my_tokens, provider_delta_tokens, relative_error, raw_artifact }` (§3.3) |
| `availability` | object | `{ deployment: serverless\|dedicated\|local, callable, probed_at, raw_artifact, error_code }`. **`callable` is measured, never inferred from a catalog price** (§13.2 finding 4). An active cloud variant with `callable: false` is a schema violation |
| `local_resources` | object \| null | required iff `locus = local`: `{ min_free_ram_gb, measured_gib_vram, watts_nominal }` |
| `provenance` | object | `{ source_url, retrieved_at, retrieved_by }` — for prices and limits. When a provider changes a price, a **new record** supersedes the old (§9) |
| `registered_at` | timestamp | |

Derivations (closed set): `filter`, `count`, `groupby`, `min`, `max`.

Prices and limits are records **with provenance**, so every historical decision stays explainable against the price frozen at decision time.

### 1.3 `ds:capabilities` — measured (variant × prompt-class) fitness

One record per `(model variant, prompt class, output budget)`. Populated **only from frozen experiment runs** — never from an assumption about a model family [litreview §3.5: eligibility is a compatibility matrix, not a difficulty score].

| Field | Type | Rule |
|---|---|---|
| `model_ref` | id | a `ds:models` variant record; MUST exist and be `active` |
| `prompt_class` | enum (closed) | §2.2. Adding a class requires a new ruleset version **and** new measurements |
| `output_budget_tokens` | number | MUST equal the measured budget. A disqualification at 8k is **not** a claim about 64k (FREEZE D5: "larger budgets are untested; claim is bounded to the tested window") |
| `n` | number | sample size. A disqualification MUST have `n ≥ policy.gates.verifiability.min_samples` |
| `measured` | object | `{ accuracy, termination_rate, schema_compliance, source_run }` — `source_run` is a frozen run id with raw artifacts |
| `eligible` | boolean | **cached derivation, never hand-set.** `eligible = eval(eligibility_derivation, measured, thresholds)` where thresholds come from the pinned policy record (§1.4). Layer 1 recomputes it (Qafiya §3.1: `value == eval(derivation, sources)`) |
| `eligibility_derivation` | string | the recipe, e.g. `termination_rate >= 0.99 and schema_compliance >= 0.90 and accuracy >= min_accuracy[prompt_class]` |
| `thresholds_snapshot` | object | the exact threshold values used, copied from the policy record at measurement time — so an old row stays checkable after thresholds change |

Derivations: `filter`, `count`, `groupby`, `min`, `max`, `avg`.

**Why `eligible` is derived.** The brief stores it as a field. Making it a recomputable derivation means ablating the verifiability gate is **editing thresholds in `ds:policy`**, not re-labelling rows — which is what the owner's "parameters become user-configurable" caveat requires, and what makes experiment 3's with/without-eligibility comparison a policy diff rather than a data rewrite.

**Two verdicts, computed deliberately differently** (implementation refinement, 2026-10-22):

- The **stored** `eligible` is recomputed from `measured` + `thresholds_snapshot` — the row's own historical verdict, which is what Layer 1 checks to prove the stored value is right and which stays stable after a policy change.
- The **routing** verdict is recomputed from `measured` + the **current** policy thresholds. Changing `min_accuracy` in `ds:policy` therefore changes routing immediately, with no capability row touched. Verified: `router/test_router.py` flips a decision by appending a policy record and asserts that no capability row changed.

**`eligibility_derivation` names a recipe; it is never evaluated as an expression.** The value is a key into a closed registry in code (`gates.ELIGIBILITY_DERIVATIONS`, currently one recipe: `termination_and_schema_and_accuracy_v1`); the *parameters* stay in data. Reason: `ds:policy` and `ds:capabilities` are user-editable data in a public AGPL repo, so `eval()`-ing a string from them would be arbitrary code execution. An unknown recipe name is a hard error, not a silent pass.

### 1.4 `ds:policy` — the decision policy

One record per policy version. **Every tunable in this layer lives here or in `ds:rules`.** Nothing below MAY be a constant in code.

```jsonc
{
  "id": "pol_v1",
  "privacy": {
    "class_order": ["public", "confidential", "private"],
    "allowed_loci": { "private": ["local"], "confidential": ["local"], "public": ["local", "cloud"] },
    "unknown_request_class": "private"          // fail closed (§2.1)
  },
  "gates": {
    "order": ["privacy", "verifiability", "context_fit"],   // FIXED by owner decision (a), 2026-10-22
    "verifiability": {
      "missing_measurement": "allow_flagged",   // refuse | allow | allow_flagged  (§2.2)
      "min_samples": 3
    },
    "context_fit": { "context_margin": 0.90, "check_local_resources": true }
  },
  "measurement": {                               // eligibility thresholds (§1.3)
    "min_termination_rate": 0.99,
    "min_schema_compliance": 0.90,
    "min_accuracy": { "tabular_summary": 0.60, "prose_summary": 0.60, "multi_doc_selective": 0.60,
                      "claim_check": 0.70, "structured_extract": 0.80, "open_chat": 0.0 }
  },
  "objective": {
    "type": "min_declared_cost",                 // lowest user-declared price wins; quality enters as a constraint above, not as a trade-off term (§3.4)
    "expected_output_tokens": {                  // per prompt class; output length is not knowable pre-call (§3.3)
      "tabular_summary": 600, "prose_summary": 600, "multi_doc_selective": 800,
      "claim_check": 200, "structured_extract": 400, "open_chat": 400 },
    "tie_break": "id_asc",                       // determinism (§7.9)
    "escalation": {
      "triggers": ["structural_check_failed", "finish_reason_length", "confidence_below_min"],  // ranked, §2.5
      "confidence_min": 0.70,
      "max_cascade_depth": 2
    }
  },
  "classifier": { "privacy_rules_ref": "rul_privacy_v1", "task_rules_ref": "rul_task_v1" },
  "budget": { "per_request_usd": null, "on_exceed": "alert_only" },   // alert_only | refuse (§2.6)
  "logging": {
    "store_prompt_content": "never_for_private",  // never | never_for_private
    "store_response_content": "never_for_private",// §12.4 — strengthens brief §5
    "store_gate_trace": "always",
    "prompt_hash": { "alg": "hmac-sha256", "key_env": "LOG_HMAC_KEY" }   // §1.6, §12.4
  }
}
```

An **experiment = (frozen battery, policy record id, rules ids, `ds:models` snapshot hash)**. Ablating the policy is editing a dataset.

### 1.5 `ds:rules` — classifiers as data

One record per ruleset version. Two kinds, same shape: an ordered rule list plus a default.

```jsonc
{
  "id": "rul_privacy_v1",
  "rule_kind": "privacy",                    // privacy | task
  "default_label": "private",                // privacy fails CLOSED; task default is "open_chat" (§2.2)
  "rules": [                                  // evaluated in order; first match wins
    { "rank": 1, "match": { "type": "keyword_any", "values": ["medical", "diagnosis", "prescription"] }, "label": "private" },
    { "rank": 2, "match": { "type": "regex", "value": "\\b(SSN|social security|bank account|password)\\b" }, "label": "private" },
    { "rank": 3, "match": { "type": "dataset_ref", "value": "ds:preference_store" }, "label": "private" }
  ],
  "label_set": ["private", "confidential", "public"],
  "validated_on": { "labelled_set": "experiments/exp2/battery/", "n": 0, "accuracy": null }  // filled by experiment 2
}
```

`match.type` is a closed enum: `keyword_any`, `keyword_all`, `regex`, `dataset_ref`, `attachment_type`. `dataset_ref` fires when the request pulls records from the named dataset — the example uses Layer 4's `ds:preference_store` (`04-memory/spec.md` §1.3), i.e. anything touching stored user preferences is private by construction. Match order is by `rank`, ties broken by array position — **no unordered evaluation**, so a decision is replayable from the ruleset alone.

**Honesty, stated up front [brief §7.1]:** the privacy gate is only as good as this ruleset. A private prompt no rule matches is labelled `private` by the fail-closed default; a private prompt that *looks* public and matches no rule is a classifier error the router cannot catch. Experiment 2 measures classifier error and gate compliance **separately**, and the paper reports both numbers.

### 1.6 `ds:decision_log` — one record per routing decision

Append-only in the strong sense: no record is ever modified or removed. Chained history per v1.3.

| Field | Type | Rule |
|---|---|---|
| `request_ref` | id | the request record (§5.1) |
| `policy_ref`, `privacy_rules_ref`, `task_rules_ref` | id | pinned versions |
| `registry_snapshot_hash` | string | sha256 over canonical JSON of the sorted `active` variant records at decision time — detects drift (§9.3) |
| `privacy_class`, `task_class` | enum | as classified, plus `classifier_matched_rank` (which rule fired; `null` = default) |
| `gate_trace` | list<object> | **one entry per gate**: `{ gate, input_count, survivors: [model_ref…], excluded: [{model_ref, reason}] }` — the full replay evidence |
| `est_tokens_in` | object \| null | `{model_ref: tokens}` for **every** variant considered, not only survivors — so gate 3 stays replayable when the prompt text is deliberately not stored (§6.1). A token *count* is not the text; storing it for private requests is part of the residual leak declared in §12.4 |
| `feasible_set` | list<object> | `[{ model_ref, est_tokens_in, est_tokens_out, expected_cost_usd }]`, sorted by `(expected_cost_usd, id)` |
| `outcome` | enum[planned, answered, escalated, refused, error] | refusal is first-class (§2.6); `planned` is a `dry_run` decision (§5.2) — a decision that ran no model MUST NOT log itself as `answered` |
| `refusal_reason` | enum \| null | §2.6 reason codes |
| `chosen` | object \| null | `{ model_ref, attempt }` — the variant whose **answer was returned**, i.e. the final attempt. The routing decision itself is `attempts[0]`, which is the `argmin` (§6.1 check 2) |
| `attempts` | list<object> | per attempt: `{ model_ref, triggered_by, est_cost_usd, actual_cost_usd, tokens_in, tokens_out, finish_reason, structural_check, confidence, wall_clock_s }` — **actuals from the provider's own meter**, never estimated |
| `prompt_hmac` | string | always stored: HMAC-SHA256 of the canonical prompt under a **local key held in `.env` (`LOG_HMAC_KEY`), never in a dataset**. Unsalted sha256 of a short private prompt is dictionary-guessable, which is itself a metadata leak [ref 33]; the key is referenced by variable name only |
| `prompt_text` | string \| null | stored only when policy allows (§12.4) |
| `response_text` | string \| null | same rule as prompt text (§12.4) |
| `budget_alert` | boolean | true if the cost cap was crossed (alert, not auto-stop, per default policy) |
| `binding_ref` | id (`b_*`) | the claim binding for this decision (§4) |

Derivations: `filter`, `count`, `groupby`, `sum`, `min`, `max`.

## 2. The decision procedure

Deterministic gates first; optimisation strictly inside the feasible set. Gates never re-run and never loosen during a cascade.

### 2.1 Gate 1 — privacy (hard)

```
cls = classify_privacy(request, policy.classifier.privacy_rules_ref)   # unknown → "private" (fail closed)
F1  = { m ∈ active_variants : m.locus ∈ policy.privacy.allowed_loci[cls]
                            and m.clearance ⊒ cls }
```

The conjunction is deliberate: `allowed_loci` is the user-tunable map (owner caveat (a)); `clearance` is the model's own property. If a user edits the map to allow `private → cloud`, no cloud variant with `clearance: public` becomes eligible — **a mis-set policy fails safe, not open.**

**Demonstrated, not just asserted** (`experiments/exp2/mechanism_long_confidential.py`, $0, 2026-10-22). A long prompt in company vocabulary classifies `confidential` while its ground truth is private. Three configurations:

| Configuration | Gate-1 exclusion reason | Result |
|---|---|---|
| `confidential → local` (default) | `locus_cloud_not_allowed_for_confidential` | refused |
| `confidential → local+cloud` (the user turns the map key) | `clearance_public_below_confidential` | **still refused** |
| map key turned **and** one cloud variant's `clearance` raised | none | routed to the cloud |

So the tunable the owner flagged is **not by itself sufficient** to expose confidential text: it takes two keys, and the second is a registry-level change rather than a convenience setting. The demonstration ran in a throwaway store, so the real registry was never modified. Boundary: this establishes the mechanism and its two-key requirement; it does not measure how often such a configuration arises, and the battery-level rate for long prompts is unmeasured.

Fail-closed has a measurable cost: over-classification pushes work local. Experiment 2 MUST report the over-classification rate alongside compliance.

**Observed during implementation, and it is not a corner case.** With ruleset v1, an ordinary unmarked business prompt — *"Summarise the totals in this table and list the top vendors"* — matches no rule and therefore classifies `private`. `classifier_matched_rank` is null, so the default path is visible in the log rather than hidden. Two consequences follow, both verified in `router/test_router.py`:

1. Ordinary work is pushed to the local arm unless the user's rules positively mark it public or confidential. The `public` label only fires on an explicit match, so a keyword ruleset needs *positive* public/confidential rules covering normal business text, not just private markers.
2. Combined with gate 3, a long unmarked prompt is **refused** rather than quietly sent to the cloud: the local window on this machine is 8,192 tokens, and a ~29,000-token table has nowhere legal to go. That is the correct behaviour under the policy — and it means the refusal rate is driven as much by the classifier's default and the local window as by model capability.

Both are reported as results. The remedy is data, not code: widen the ruleset, or raise the local runtime window, or both — and experiment 2 measures the effect of each.

### 2.2 Gate 2 — verifiability (eligibility)

```
ttype = classify_task(request, policy.classifier.task_rules_ref)        # default "open_chat"
F2    = { m ∈ F1 : eligible(m, ttype, m.output_budget_tokens) }
```

`prompt_class` is a **closed enum**, aligned to the frozen batteries this layer reuses:

| Class | Source battery |
|---|---|
| `tabular_summary` | efficiency-experiment S1/S2 tabular decks |
| `prose_summary` | efficiency-experiment prose + text-layer PDF decks |
| `multi_doc_selective` | efficiency-experiment S2 selective-loading scenario |
| `claim_check` | defect-eval: 68 labelled injected defects, model as checker |
| `structured_extract` | schema-valid JSON out (defect-eval corpus → extraction) |
| `open_chat` | default when no task rule matches |

Per §1.3, `eligible` is a recomputed derivation. When no capability row exists for `(m, ttype, budget)`, `policy.gates.verifiability.missing_measurement` decides: `refuse` (strict; empties the set for unmeasured pairs), `allow` (silent), or `allow_flagged` (**default** — allowed, and the decision is counted against eligibility coverage, §6.3). Strict mode is what experiment 3 ablates.

### 2.3 Gate 3 — context_fit

```
F3 = { m ∈ F2 : est_tokens_in(m) + expected_output(m) ≤ context_margin × m.context_window }
```

`est_tokens_in(m)` is per-variant because tokenizers differ (§3.3). **Interpretation flagged for the owner (§12.2):** when `check_local_resources` is true, gate 3 also excludes local variants whose `min_free_ram_gb` exceeds measured free memory. This is not a fourth gate and not a new currency — it is feasibility, folded into `context_fit` so the owner-confirmed gate *order and names* are untouched. A 16 GB machine cannot serve a 9 GB model regardless of price; without this check the router selects a variant that then fails at runtime, which is an `error`, not a decision.

### 2.4 Objective — cost, inside the feasible set only

```
if F3 = ∅: return REFUSE(reason = the gate that emptied F)          # §2.6
c = first( sort(F3, key = (expected_cost(m), m.id)) )                # argmin, deterministic tie-break
```

Cost never overrides a gate: the objective is evaluated **only** over `F3`. Expressed in the Qafiya closed derivation set as `filter(F3) → min(expected_cost_usd)` with the winner recovered by `filter(expected_cost_usd == min)` and tie-broken by `id` ascending (§12.3).

### 2.5 Cascade — escalation on machine-checkable signals first

```
ans = run(c, request); depth = 1
while should_escalate(ans) and depth < policy.objective.escalation.max_cascade_depth:
    F3' = F3 minus already-tried variants                 # gates are NOT re-run
    if F3' = ∅: break
    c = argmin over F3'; ans = run(c, request); depth += 1
```

`should_escalate` consumes `policy.objective.escalation.triggers` **in rank order**. The ranking is a design claim, not a detail:

1. `structural_check_failed` — the answer violates the schema/binding discipline it was asked for. Objective, deterministic, free.
2. `finish_reason_length` — the model consumed its completion budget without answering. Objective, and it is **exactly** the measured reasoning-mode hazard (FREEZE D5). Escalating on it turns a hazard into a recoverable event.
3. `confidence_below_min` — the model's self-reported confidence. Subjective; the dominant signal in the literature [refs 20, 36, 45] and the weakest of the three.

Triggers 1–2 are checked before 3 because a calibrated-uncertainty signal from a small model is the least trustworthy input available, and this layer has a cheaper, harder one.

**A property worth stating, measured 2026-10-22.** Because escalation re-ranks the *same* feasible set by cost and never re-runs the gates, a cascade exhausts zero-priced local capacity before it spends anything: with two $0 local arms registered, an escalation from the 3B goes to the 8B, not to the cloud. That is the desirable behaviour — and under the shipped policy it is also the rule: local connections are declared $0, so they win by default whenever they are feasible (RC-3.2). Pinned by `router/test_acceptance_rc.py`, which asserts the escalation target is the next entry of the ranked feasible set — whatever its locus — so the test does not silently encode today's connect list.

### 2.6 Refusal — a typed outcome

Refusal is a result (PROJECT.md rule 3), logged with a reason code and the gate trace that produced it. **The refusal rate is a headline number, not an embarrassment.**

| Reason code | Meaning |
|---|---|
| `no_variant_for_privacy_class` | gate 1 emptied the set (e.g. private request, no local variant registered) |
| `no_eligible_variant_for_class` | gate 2 emptied it (strict `missing_measurement: refuse`, or all measured ineligible) |
| `context_exceeds_all` | gate 3: request too long for every feasible variant |
| `local_resource_unavailable` | gate 3: no local variant fits the machine and cloud is not permitted |
| `budget_exceeded` | only when `budget.on_exceed = refuse`; default is `alert_only` |
| `classifier_default_fail_closed` | no privacy rule matched and no variant satisfies the fail-closed class |

### 2.7 Determinism

Same request + same frozen datasets ⇒ **byte-identical decision record** (modulo `id` and timestamps). Determinism is claimed for the *decision*, never for the model's answer. This is what makes the audit in §6.1 a proof rather than a re-run.

## 3. Cost model

### 3.1 The unit trap (normative)

`price.input_per_mtok` / `output_per_mtok` are **USD per one million tokens**, and Together's `pricing.input` / `pricing.output` are already in that unit. Values are stored **as returned by the API**; multiplying by 1e6 yields $300,000/Mtok for a $0.30 model — wrong by a factor of a million, and already committed once.

Mechanical guard, enforced at registration and re-checked by Layer 1: reject any price with `*_per_mtok > 1000` as a suspected unit error. The measured spread across Together's paid chat catalog is **$0.02 → $3.50 per Mtok** (175×, HANDOFF §3), so 1000 is two orders of magnitude above the real ceiling and cannot reject a legitimate price.

**Store the provider's float representation verbatim.** Together's API returns prices as binary floats with representation noise — the 2026-10-22 snapshot literally returns `0.060000000000000005` for `meta-llama/Llama-3.2-3B-Instruct` and `1.0399999999999998` for `Llama-3.3-70B-Instruct-Turbo`. Rounding them at registration would misrepresent the source and break the "explainable against the price frozen at decision time" guarantee (§1.2). Rounding is a **presentation** act, applied in the paper and never in `data.jsonl`. Determinism is unaffected: `argmin` over these floats is exact, and exact ties fall to the `id_asc` tie-break (§2.4).

**Tiered pricing is registered as a band, and the band becomes a routing constraint.** Some providers price by input length: DashScope's published international rates for `qwen-flash` are $0.05/$0.40 per Mtok for 0–256K input tokens and $0.25/$2.00 for 256K–1M — a 5× jump at the boundary. Rather than model a price function, the registry stores the applicable band in `price.tier` and sets `context_window` **at the band boundary** (262,144), even though the model accepts 1M. Gate 3 then keeps every request inside the cheap band, so a prompt that would cross it is refused rather than silently billed at 5×. A request that genuinely needs the upper band is a new record with a new tier — succession (§9.1), not an edit.

### 3.2 Local connections cost what the user declared — $0 by default

A local connection's API price is $0, and this spec does not manufacture a valuation on the user's behalf. The pre-RC draft carried two policy knobs — `local_shadow_price_per_mtok` (an energy estimate) and `latency_price_usd_per_s` (a valuation of waiting) — to stop the frontier collapsing onto free local capacity; RC-3 removed both. The objective among feasible connections is `min_declared_cost` with a deterministic `id_asc` tie-break, so **local wins by default whenever it is feasible — the intended product behaviour, not a defect** (RC-3.2).

Consequences, stated plainly:

- **Escalation past a local connection needs a machine-checkable signal**: no eligible local connection, a context/machine-fit failure, or a failed call (`timeout`, `connection`, `server`, `malformed_response`). Self-reported confidence is a secondary, optional trigger and never outranks these (RC-3.3).
- **The cost–quality frontier stays in experiment 1 as analysis** (always-small / always-big / routed, local at $0), not as the product's decision rule. Any crossover analysis is a sensitivity curve in the paper over a declared local-price valuation, clearly labelled as the user's valuation, and is not a policy input; figures must not imply local has a measured price (RC-3.4).
- **Cost is reported as two numbers — actual spend from provider meters and list-price estimate — never conflated** (RC-3.5).
- An unpriced connection (`price_source: "unknown"`) is unrankable by cost: it ranks last and the decision record says so (RC-1.4).

### 3.3 Token estimates are per-variant

`est_tokens_in` MUST come from the variant's own `tokenizer_ref`, never from a shared tokenizer and never from character or word counts. The efficiency experiment measured the same text ~30% cheaper under Llama's tokenizer than Qwen's (FREEZE, live arm) — a shared estimate would mis-rank two cloud models by a third.

**Frozen and independently reproduced.** `tokenizer/manifest.json` pins two tokenizer files by sha256 with their source URLs (Llama-3, vocab 128,256; Qwen2.5, vocab 151,665). Counting the upstream frozen corpus file `expenses_large.csv` with both gives 16,909 vs 23,942 tokens — Llama at **0.7062** of Qwen, against the **0.7101** implied by the upstream live S2 numbers (47,886 / 67,432). The ratio reproduces to within 0.4%, which validates both the tokenizer artifacts and the upstream measurement.

One refinement that measurement forced: the gap is **content-dependent**. On dense numeric CSV it is 29.4%; on prose (`report_quarterly.md`) it is only 5.0%. So per-variant tokenizers matter most exactly where this layer's batteries live — tabular prompts — and a paper claim of "30% cheaper" without naming the content type would overstate it.

**Validated directly against a provider's meter, not just against another tokenizer.** `router/probe_tokenizer_agreement.py` cancels the chat template's fixed overhead by differencing two calls (prompt A, then A+corpus) and compares the provider's metered increase with the frozen tokenizer's count of the corpus alone. Against DashScope's meter for `qwen-flash`: **6,008 metered vs 6,008 counted — 0.00% error**, while the Llama tokenizer on the same text is off by −29.43%, a third independent confirmation of the cross-family gap. End to end through `route()`, the input-token estimate landed within **−0.05%** of the meter (`experiments/registry_probe/dashscope_e2e_*.json`). Each variant's measured error is stored in the registry (`tokenizer_agreement`), so the estimate-vs-actual number in the paper is a record, not a recollection.

`router/tokens.py` **fails closed**: a missing tokenizer, a checksum mismatch against the manifest, or an unavailable `tokenizers` package raises rather than substituting an approximation, because a silent approximation would corrupt every cost estimate and every gate-3 decision downstream. Verified by running it under an interpreter without the package. `est_tokens_out` comes from `policy.objective.expected_output_tokens[prompt_class]` — a declared per-class default, because output length is not knowable pre-call. It is a policy field, so an arm that changes it is an ablation, not a code edit.

Every attempt records **actual** usage from the provider's own meter; the spine computes estimate-vs-actual error and the paper reports it [brief §7.3].

### 3.4 Quality is a constraint, not a trade-off term

`objective.type = min_declared_cost`: the objective is pure declared cost. Quality enters through `policy.measurement.min_accuracy[prompt_class]` inside eligibility (§1.3). Consequences:

- **"Cost never overrides a gate" is structurally true**, not a promise — there is no quality term for cost to outweigh.
- **The cost–quality frontier is a policy sweep.** Varying `min_accuracy` traces the frontier; each point is a policy record, so every point on the curve is reproducible from data and the crossover is attributable to a named threshold.
- The greedy cheapest-first cascade is **not** cost-optimal under uncertainty [refs 8, 23]. Stated as a limit (§10.5); `objective.type` is data, so an alternative ordering is ablatable without code.

## 4. The decision binding

Every decision record is joined to its evidence by a Qafiya `claim` binding (Qafiya spec §2) written to `datasets/decision_log/bindings.jsonl`. Two kinds of claim are bound, at two granularities, because they are proved differently.

### 4.1 Per-decision binding — the cost and the locus

```jsonc
{
  "id": "b_…",
  "kind": "claim",
  "target": { "dataset": "ds:decision_log", "record": "dec_…" },   // extends Qafiya §2's {view, node}; see §12.3
  "sources": [                                   // MUST be non-empty; each resolves (Qafiya §7.2)
    { "dataset": "ds:policy",   "record": "pol_v1",         "field": "gates" },
    { "dataset": "ds:models",   "record": "mdl_…",          "field": "price" },
    { "dataset": "ds:rules",    "record": "rul_privacy_v1", "field": "rules" }
  ],
  "derivation": "min(expected_cost_usd) filter(active_variants, privacy ∧ verifiability ∧ context_fit)",
  "value": 0.0063,                                 // USD; MUST equal eval(derivation, sources)
  "statistic": "min",
  "device": "local-m1pro",                         // claim tuple: which run produced it
  "authoritative_artifact": "datasets/decision_log/data.jsonl",
  "assertion": {
    "statement": "the locus chosen for this private-classified request is one the policy permits",
    "predicate": "label_match",                    // S2-class: right value, right thing
    "expected": ["local"], "tolerance": null,       // the permitted set, so it also works for public
    "both_sides": { "allowed_loci": ["local"], "chosen_locus": "local" }   // comparisons record both values
  },
  "provenance": { "created_by": "llm:deewaanai-l5-router", "created_at": "…" },
  "check": { "provenance": "PASS", "semantic": "PASS", "checked_at": "…" }
}
```

The binding proves the **minimum cost** is recomputable from pinned data. It does not need to name the winner: `value` is a number, so `min` from the closed derivation set suffices, and the winner's identity is proved separately by §6.1 check 2 (recompute `argmin` over `feasible_set` under the declared tie-break). Keeping `argmin` out of the binding is what lets this layer conform to Qafiya §1.2's closed set with no substrate change (§12.3).

Three jobs, per Qafiya §2: the decision's `value` is **recomputed** from the pinned sources (render/audit), a policy edit is an `edit` binding whose `writeback` names the exact record and field (durable edit), and the `assertion` is checked against its own evidence (claim). Because `gate_trace` records every survivor and exclusion per gate, recomputation needs no live provider call and no trust in the router's own code path.

### 4.2 Aggregate binding — the paper's headline claim

The privacy-compliance number is a claim over the whole log, not over one decision, so it gets its own binding, sourced from `ds:decision_log` and recomputed by the spine:

```jsonc
{
  "id": "b_…", "kind": "claim",
  "target": { "dataset": "ds:decision_log", "record": null, "scope": "exp2/run_1" },
  "sources": [ { "dataset": "ds:decision_log", "filter": "run=exp2/run_1", "field": "privacy_class" } ],
  "derivation": "count(filter(decision_log, privacy_class='private' and chosen.locus='cloud'))",
  "value": 0,
  "statistic": "count",
  "device": "local-m1pro",
  "authoritative_artifact": "datasets/decision_log/data.jsonl",
  "assertion": {
    "statement": "no private-classified request was routed to a cloud variant",
    "predicate": "scope_enumeration",              // S3-class: ONE counterexample fails it
    "expected": 0, "tolerance": 0,
    "both_sides": { "private_requests": "<n, filled by exp2>", "private_routed_to_cloud": 0 }
  }
}
```

`both_sides` is filled from the frozen log at spine time, never hand-written: a compliance rate without its denominator is the S5 defect (exploratory-as-confirmed) waiting to happen.

### 4.3 Registry pins

`ds:models` and `ds:capabilities` rows are pinned **by record id** in `sources`, so a later price change cannot retroactively alter what a past decision meant. `registry_snapshot_hash` is stored alongside as a drift detector, not as the source of truth.

## 5. Interface exposed to Layers 2–4 and 6

Layers above never pick models themselves [brief §8].

### 5.1 Request

```jsonc
{ "request_id": "req_…", "text": "…", "attachments": [{"type": "csv", "sha256": "…", "bytes": 0}],
  "est_tokens_hint": null, "requires_binding": true, "privacy_hint": null, "task_hint": null,
  "budget_usd": null, "dry_run": false }
```

`privacy_hint` / `task_hint` are **advisory only**: the classifier's verdict is recorded either way, and a hint that disagrees with it is logged as a disagreement (a free classifier-error signal).

### 5.2 Response

```
route(request) → { outcome: planned | answered | escalated | refused | error,
                   decision_ref: "dec_…", binding_ref: "b_…",
                   chosen: {model_ref, backend} | null,
                   refusal_reason: … | null,
                   cost: {estimated_usd, actual_usd}, answer: … | null }
```

`dry_run: true` runs gates 1–3 and writes the decision record **without calling any model**, with `outcome = planned`. This is normative for experiment 2: privacy compliance is a property of the *decision*, so 100%-compliance is measured over the whole labelled battery at **zero cloud spend**. Only experiments 1 and 3 require paid calls.

## 6. Verification

### 6.1 Layer 1 — data map (deterministic; MUST; a proof)

A **replay audit** over the frozen decision log. For every decision record:

1. **Gate replay:** re-run gates 1–3 from the pinned policy/rules/registry records; the recomputed survivor sets MUST equal `gate_trace` exactly (per gate, per excluded model, per reason).
2. **Choice recompute — two distinct facts.** The **first attempt** MUST be the `argmin` of the recomputed feasible set under the recorded tie-break (that is the routing decision), and **`chosen`** MUST be the *final* attempt (that is the variant whose answer was returned). In a cascade these differ by design. Conflating them makes every escalated decision look like a routing error — this ambiguity was found by the audit itself on 2026-10-22 and is the reason the check is stated as two clauses.
3. **Eligibility recompute:** every `ds:capabilities.eligible` MUST equal `eval(eligibility_derivation, measured, thresholds_snapshot)`.
4. **Price sanity:** no `*_per_mtok > 1000` (§3.1); every `price` has `provenance.source_url` and `retrieved_at`.
5. **Chain integrity:** `qafiya_core.chain.verify` on all five datasets' `history.jsonl`.
6. **Reference resolution:** every `model_ref`, `policy_ref`, `*_rules_ref`, `source_run` resolves to an existing record; no binding sources from an artifact in `SUPERSEDED.json`.
7. **Privacy compliance:** count of decisions with `privacy_class = private` and `chosen.locus = cloud` MUST be **0**. One counterexample fails the audit — this is the S3 scope-enumeration check, and it is the paper's headline number, measured not asserted.
8. **Cost actuals:** every `attempts[].actual_cost_usd` MUST be recomputable from the provider-metered token counts and the pinned price record.

Binary outcome: exit 0 or it does not. There is no percentage to negotiate with.

### 6.2 Log-privacy proof-of-absence scan

The log is itself a leak surface [ref 33]. Borrowing Layer 4's proof-of-absence pattern (`04-memory/spec.md` §5.2): a verification pass MUST scan **every field of every decision-log record and every `history.jsonl` entry** for the canonical strings of the frozen private prompts, and assert absence. Result (PASS/FAIL + scanned artifact sha256s + scan coverage) is stored as a bound record. This catches what a schema rule cannot: a private prompt smuggled into `gate_trace.excluded[].reason`, a refusal message, or an `error` string.

Fields scanned MUST be enumerated in the record — an unscanned field is reported, not assumed clean.

**The scan runs at two scopes, because they answer different questions.** *Scoped* to one run: did this policy leak? *Standing* over the whole log: what is in there today? A policy change can stop future leakage without being able to retract past leakage — under append-only provenance the earlier text stays in `data.jsonl` and in the chained `history.jsonl` forever, so the standing scan keeps failing after the fix. Reporting only the scoped scan would hide that; reporting only the standing one would hide whether the fix works. Measured on 2026-10-22: policy v2 stops the leak (scoped clean, 0/44 decisions storing text) while the standing scan still finds prompt `p11`, written by the v1 run (§10.11).

A scan also needs a **non-vacuity control**: it MUST find a string that is known to be present (the run label, or a prompt the policy does store). Without that control, a passing absence scan is indistinguishable from a broken one.

### 6.3 Coverage (Qafiya §4 analogue)

Reported alongside every audit run, never folded into a pass rate:

- **Eligibility coverage** — share of `(variant, prompt_class)` pairs on feasible paths that have a frozen capability row. A decision made under `allow_flagged` lowers it visibly.
- **Binding coverage** — share of decision records with a resolvable claim binding (target: 100%; anything less is I1).
- **Privacy-label coverage** — share of battery prompts whose class came from a matched rule rather than the fail-closed default.

### 6.4 Layer 2 — semantic map (advisory; SHOULD; a flag)

| Check | Class |
|---|---|
| A capability row whose `source_run` is a superseded or discredited run | N2 stale-version → quarantine + transitive invalidation (§9.2) |
| A decision whose recorded `privacy_class` disagrees with `privacy_hint` or with a re-run classifier | S2 wrong label → flag for human review |
| Paper prose claiming "always local" / "never escalates" where one record disagrees | S3 over-claim → scope enumeration |
| A claimed hazard with no bound `source_run` artifact | S4 phantom analysis → existence gate |
| Eligibility measured on `n = 1` | S5 exploratory-as-confirmed → flag; the gate status is part of the claim |
| Mechanism claims ("reasoning models enumerate rows because …") | **never auto-pass**; evidence recorded, judgment handed to a human (R6) |

### 6.5 The spine — every headline number, recomputed

`experiments/spine.py` recomputes each number below from raw artifacts (decision logs, provider usage JSON, frozen batteries) and exits non-zero if any fails. **If the spine fails, the number does not go in the paper** (PROJECT.md rule 2). Pattern follows `04-memory/experiments/spine.py`.

| Experiment | Headline numbers the spine MUST recompute |
|---|---|
| 1 — cost–quality frontier | per-arm measured cost (USD) and quality; the crossover as a **sensitivity curve over a declared local-price valuation** (paper analysis, labelled the user's valuation, never a policy input — RC-3.4); estimate-vs-actual cost error per variant (§3.3) |
| 2 — privacy compliance | violations / private requests (both sides, §4.2); classifier accuracy on the labelled set; over-classification rate (§2.1); proof-of-absence scan result + scanned-artifact hashes + scan coverage (§6.2) |
| 3 — verifiability interaction | per-(variant, class) accuracy / termination rate / schema compliance with `n`; eligibility coverage (§6.3); refusal rate broken down by §2.6 reason code; routed-with-eligibility vs routed-without; the reasoning-hazard termination rate at each tested output budget; the adversarial cost bound (§10.8) |

Negative and uncatchable results are recomputed too: a 100% refusal rate on `tabular_summary` (§10.1) is a spine output, not a suppressed case.

## 7. Well-formedness invariants (testable)

1. Every record carries the §1.1 base fields; ids are prefixed, sortable, immutable; content edits never change an id.
2. Every `ds:models` variant carries complete `provenance` (cloud: `source_url` + `retrieved_at`; local: the measurement that produced `local_resources`); every price passes the §3.1 unit guard.
3. Every `ds:capabilities` row's `model_ref` resolves to an `active` variant, and its `output_budget_tokens` matches that variant's.
4. `eligible` recomputes from `measured` + `thresholds_snapshot` for every row, at every audit.
5. Every decision record pins `policy_ref`, both ruleset refs, and `registry_snapshot_hash`.
6. `gate_trace` has one entry per gate in `policy.gates.order` **up to and including** the gate that emptied the set — a refusal carries a truncated trace, and the last entry's `survivors` is empty. Replay reproduces it exactly, truncation included.
7. No decision with `privacy_class = private` has `chosen.locus = cloud` (0 violations; hard fail).
8. No decision record for a private request contains `prompt_text` or `response_text`; the §6.2 scan is PASS.
9. Given identical frozen inputs, the decision is byte-identical including tie-breaks.
10. Every `attempts[]` entry carries provider-metered actuals; `actual_cost_usd` recomputes from tokens × pinned price.
11. Refusals carry a reason code from the §2.6 enum and a gate trace naming the gate that emptied the set.
12. All five datasets' `history.jsonl` verify under `qafiya_core.chain`.
13. No binding sources from `SUPERSEDED.json`; every binding's `sources` is non-empty and resolves.
14. Coverage (§6.3) is reported with every audit result; an unmeasured pair is never silently treated as eligible.

Each maps to a functional test (F1–F14) and a paper claim.

## 8. Storage layout

```
datasets/models/          schema.json data.jsonl history.jsonl bindings.jsonl SUPERSEDED.json
datasets/capabilities/    …
datasets/policy/          …
datasets/rules/           …
datasets/decision_log/    …
router/
  qafiya_core/chain.py    # VENDORED from the frozen v1.3 package — imported, never reimplemented
                          #   (adoption pattern: 04-memory/prototype/qafiya_core/chain.py)
  registry.py             # load/validate ds:models + ds:capabilities; the only price reader
  policy.py               # load/validate ds:policy + ds:rules; no tunable may be a literal here
  gates.py                # gates 1–3, pure functions over frozen data
  backends/ollama.py      # local
  backends/together.py    # cloud
  decide.py               # route(); dry_run supported (§5.2)
  audit.py                # Layer-1 replay audit (§6.1) + proof-of-absence scan (§6.2)
experiments/exp{1,2,3}/   frozen batteries, raw runs, derived numbers
experiments/spine.py      # recomputes EVERY headline number from raw artifacts
.env                      # gitignored (already in place, first commit); TOGETHER_AI_API_KEY, LOG_HMAC_KEY
.env.example              # committed template (names only, no values)
```

The `.env` key is loaded by explicit path. Its value is never printed in a log, a paper, or a chat — variable names and lengths only. Because rule 6 of PROJECT.md puts this code on a **public AGPL-3.0 repo**, `.gitignore` MUST be present in the first commit; a published key is unrecoverable.

## 9. Versioning, supersession and invalidation

### 9.1 Price and limit changes are succession, not quarantine

When a provider changes a price, a **new** `ds:models` record is appended and the old one set `status: retired` with `superseded_by`. The old record is **not** added to `SUPERSEDED.json`. This distinction is load-bearing: quarantine invalidates every binding that sources from the artifact (Qafiya §3.1), so retiring a price by quarantine would fail Layer 1 on every historical decision that used it — destroying exactly the property §1.2 promises (decisions explainable against the price frozen at decision time). Bindings pin record ids; succession leaves them valid.

### 9.2 Quarantine is for discredited measurements

`SUPERSEDED.json` is reserved for artifacts that were **wrong**, not merely old: a capability row from a buggy run, a corpus with a broken checksum, a run whose chain failed. Quarantining one triggers Qafiya §6 transitive invalidation — every decision binding that sourced it goes `provenance = PENDING`, and every headline number derived from those decisions MUST be recomputed by the spine before it re-enters the paper. A capability row invalidated this way is `status: discredited`, never deleted.

### 9.3 Drift detection

`registry_snapshot_hash` is recomputed at audit time against the pinned records. A mismatch means the registry moved under a frozen decision — reported as a defect, never repaired silently.

## 10. Honesty section — what this router provably cannot do

Stated in the paper; measured where possible.

1. **Privacy classification bounds everything.** The gate is a set filter over frozen data and is exactly as good as `ds:rules`. A private prompt that matches no rule and reads as public is routed to the cloud, and the router cannot catch it. Classifier error is measured on the labelled set and reported **separately** from gate compliance; conflating them would report a 100% gate pass on top of a broken classifier.
2. **Fail-closed costs money — and can cost the answer entirely.** Defaulting unmatched requests to `private` pushes work to a weaker local arm. The over-classification rate and its quality cost are reported, not hidden. Measured during implementation (§13.4): on a private medical prompt the local 3B arm **declined to answer at all** on safety grounds. So the privacy gate's price is not only "a weaker answer" but sometimes "no answer", and that price is model-specific — which is an argument for a larger local arm, and a number experiment 2 must report alongside compliance.
3. **A local connection's $0 price is the user's declaration, not a measurement.** The router prices what the user declared and does not meter energy or time; a user who wants waiting or watts priced into routing declares that price on the connection record (RC-3). Apple Silicon energy measurement needs root (`powermetrics`); this spec does not claim it.
4. **Cost estimates are estimates.** Cloud token counts are known post-hoc; the frontier is reported on **measured** cost, with estimate-vs-actual error stated per model [brief §7.3].
5. **Greedy cheapest-first is not cost-optimal** under uncertainty [refs 8, 23]. Reported as a limitation of `objective.type`, ablatable as data.
6. **Eligibility is per frozen measurement, per budget.** A silent provider update can invalidate a capability row; provenance fields make it *detectable*, not preventable [brief §7.4]. A disqualification at an 8k budget is not a claim about 64k.
7. **No protection against a malicious or compromised local model.** The threat model covers data leaving the device, not local behaviour [brief §7.2].
8. **The soft optimiser is adversarially steerable; the gates are not** [refs 42, 43]. A deterministic gate cannot be sweet-talked, so the attack surface is the cost objective and the classifier. Worst-case adversarial cost inflation is bounded by `max_cascade_depth × max(expected_cost over the feasible set)`, and that bound is computed and stated, not hand-waved.
9. **The hash chain detects modification, not a rewriting attacker** who recomputes it (Qafiya v1.3 §5 limit 5). External anchoring is future work.
10. **The cost model prices declared tokens, not access.** Prices are user declarations (RC-1.4), and the objective is blind to rate limits, quotas, and latency SLOs — all real properties of the endpoints a user may connect. A $0-declared cloud connection wins every `min_declared_cost` comparison; if a free tier throttles, the failure surfaces as a typed error and the breaker, not as a price signal. **The router optimises declared price per token and nothing else.**
11. **A leak into the log is permanent.** Append-only, hash-chained provenance is what makes decisions auditable, and it is also what makes a retraction impossible: once a private prompt is written to `data.jsonl` and `history.jsonl`, removing it means rewriting history and recomputing the chain — the exact operation the chain exists to detect. Measured, not hypothetical: experiment 2's v1 run stored prompt `p11` because the logging rule was keyed to the classifier's own verdict; policy v2 stopped the mechanism, and the standing scan still finds the text. **The only remedy is not writing it in the first place**, which is why §12.4 makes the default `never` for all classes rather than trusting a classifier. Layer 4 documented the same boundary for memory payloads; this layer inherits it.

### 10.1 Pre-registered risk: `tabular_summary` may have no eligible model

The upstream live runs already measured two facts that bear directly on gate 2:

- Both cloud models failed **every** in-head aggregation check in **both** arms — 0/12 each — hallucinating plausible-but-wrong sums whether given raw files or structured data (`efficiency-experiment/FREEZE.md`, surprise finding H4).
- Two reasoning-mode variants consumed their entire completion budget on every large tabular prompt without answering — 24/24, `finish_reason: length` (note D5).

If experiment 3 measures `accuracy ≈ 0` on arithmetic-bearing tabular prompts for every variant, then `min_accuracy[tabular_summary] = 0.60` empties the feasible set **by construction** and the refusal rate for that class is 100%. That is a result, not a bug, and it is reported as one. The design response, already established upstream, is that **totals are served by bindings, not generated by the model** — so the battery items MUST be defined so the model's job is composition over supplied, Layer-1-verified aggregates. Both variants are run and reported: the binding-served form (the production path) and the in-head form (the hazard measurement). This spec does not choose the outcome in advance.

## 11. Non-goals

Not what the model says (Layers 2–4). Not the shell or kernel loop (Layer 6). Not a learned router: a learned scorer is implemented **only if** the deterministic frontier shows a measurable gap worth closing — the literature predicts it will not, for coarse task types [ref 17]. Not multi-provider load balancing, throughput optimisation, quantization or batching. Not a general policy engine: `ds:policy` expresses routing, and a new gate kind requires a spec version bump. Not automatic teardown or auto-stop on cost: caps alert, humans decide (HANDOFF §9).

## 12. Deltas from `brief.md`, and decisions the owner must make

Nothing here silently changes a confirmed decision. Items 12.1–12.3 are contract-level and go to the **monitor** (root `INTERFACES.md` is monitor-owned); 12.4–12.8 are for the **owner**.

### 12.1 Identity prefixes → monitor registration required

`INTERFACES.md` fixes the OS-wide convention (prefixed sortable ids; prefix declares the owning layer) but assigns Layer 5 none. **Proposed:** `mdl_*` models, `cap_*` capabilities, `pol_*` policies, `rul_*` rulesets, `dec_*` decisions, `req_*` requests; bindings stay `b_*`. Fallback if the monitor prefers one prefix per layer: `rt_*` for all six kinds, with `kind` disambiguating. Requested action: the monitor registers whichever is chosen. This spec uses the proposed set; a rename is mechanical and touches no claim.

### 12.2 Gate 3 covers machine fit, not only context fit → owner confirmation

§2.3 folds local-resource availability into `context_fit` rather than adding a fourth gate, so the owner-confirmed gate order and names `privacy → verifiability → context_fit` are preserved. Alternative if the owner prefers literal fidelity: keep gate 3 as context-only and let an unfit local variant surface as an `error` outcome. **Recommendation: fold it in.** A refusal with a reason is auditable; a runtime OOM is not.

### 12.3 Two substrate-shape questions → monitor ruling

**(a) `argmin` vs the closed derivation set.** Qafiya §1.2 closes the set (`sum, avg, count, min, max, groupby, filter`) so every derivation is machine-recomputable. Routing needs "the record attaining the minimum", which `min` does not return. **This spec stays inside the closed set:** bindings carry `min(expected_cost_usd)` over a `filter`ed set and assert the *number*; the winner's identity is proved by the §6.1 replay check over `feasible_set` with a deterministic `id_asc` tie-break (§2.4, §4.1, §7.9). No contract change is required. If the monitor prefers explicitness, the alternative is adding `argmin(field)` at the next bump — one line, but a substrate change and therefore the monitor's call, not this layer's. Nothing here depends on it.

**(b) Binding `target` shape.** Qafiya §2 defines `target` as `{view, node}`. Layer 5 renders no views — a decision binds to a *record*, so §4 uses `{dataset, record}` (plus `{scope}` for the aggregate claim in §4.2). This is a shape extension, not a new mechanism: the target still names exactly one thing, and Qafiya §7 invariant 1 (one binding per data-bearing node) has no view to apply to here. Requested action: the monitor either registers `{dataset, record}` as an admissible target form, or directs Layer 5 to wrap decisions in a trivial view. **Recommendation: register the form** — Layers 3, 4 and 6 all produce records rather than views, so the question will recur.

### 12.4 Response content follows the prompt's privacy rule → owner confirmation (strengthens brief §5)

The brief requires that a private request's **prompt text** never be stored. Its *response* can carry the same content ("your medical bill totals $X"), so `logging.store_response_content` applies the identical rule, and refusal/error strings are covered by the §6.2 scan rather than by a schema field. Two further points, both from [ref 33]:

- **The brief's "content hash" is not safe unsalted.** A short private prompt hashed with plain sha256 is recoverable by dictionary attack, so the hash leaks what it was meant to protect. §1.6 replaces it with HMAC-SHA256 under a local key held in `.env` (`LOG_HMAC_KEY`, name only in `.env.example`, value never committed nor printed). Action at the prototype step: add the variable name to `.env.example`.
- **Token counts, model, and timestamp are themselves a fingerprint.** The log stores them because the cost claim is impossible without them; the paper states this residual leak plainly instead of claiming a leak-free log.

### 12.5 `eligible` becomes a derived field → owner confirmation (strengthens brief §3.2)

§1.3. Consequence: threshold ablation is a policy edit, and Layer 1 can recompute every eligibility verdict. Cost: capability rows carry `eligibility_derivation` + `thresholds_snapshot`.

### 12.6 ~~Local shadow price becomes an explicit policy knob~~ → **superseded 2026-10-23 by RC-3**

*Kept as the record of what was believed on 2026-10-22.* The owner confirmed both knobs that day; `required_changes.md` RC-3 then removed them: local connections cost what the user declared ($0 by default), and the current §3.2 is the normative text. Original reasoning: without a shadow price the frontier collapses (local is free, local always wins) and experiment 1 has nothing to measure; RC-3.4 answers this by moving the frontier to the paper as analysis, with the crossover a labelled sensitivity curve rather than a policy input.

### 12.7 `missing_measurement` default is `allow_flagged`, not `refuse` → owner confirmation

§2.2. Strict `refuse` is honest but empties the feasible set for every unmeasured pair at boot, making the router unusable until a full matrix is measured — and on this machine a full matrix costs real money. `allow_flagged` keeps the router usable and makes the gap a **reported coverage number** (§6.3). Experiment 3 runs both and reports the difference, which is the measurement the choice deserves.

### 12.8 Two open costing questions before any paid run

- Which cloud variants enter `ds:models` for experiment 1? Together's catalog is 269 models / 176 chat (78 paid, 98 priced $0); the paid input spread is $0.02–$3.50/Mtok. A 3–5 variant registry spanning that range is enough to show a frontier and cheap to pilot.
- Which local variants? **3B–8B Q4**, not the brief's illustrative `Qwen3-14B-Q4` (~9 GB on a 16 GB machine — a spec example, not a validated choice; HANDOFF §4.2). `llama3.2:latest` (3B) is already present and verified working.

Per HANDOFF §8.3: pilot small, report actual cost, then ask before the full run. Cloud arms spend real money.

## 13. Implementation status and environment findings (2026-10-22)

Recorded here so the monitor can audit claims against artifacts, and so the next agent does not re-discover them.

### 13.1 Built and verified

| Artifact | Status |
|---|---|
| `datasets/{models,capabilities,policy,rules,decision_log}/` | full §8 filesets; schemas follow Qafiya §1.2 with typed fields, closed enums and declared invariants |
| `router/qafiya_core/chain.py` | **vendored byte-identical** from the frozen v1.3 package (sha256 verified against all three copies in the tree) |
| `router/store.py` | chained append, atomic rewrite, schema validation, binding validation, quarantine, snapshot hash — `router/test_store.py`, **20/20 pass** including tamper detection |
| `router/register_models.py` | registry population from frozen provenance; unit guard active; succession (not quarantine) on change; idempotent on re-run |
| `router/probe_cloud.py` | measures which cloud variants are actually callable; `router/probe_local.py` does the same for the local arm |
| `router/seed_policy.py` | policy v1 + privacy/task rulesets v1, all tunables in data |
| `router/classify.py`, `gates.py`, `cost.py`, `tokens.py`, `decide.py` | gates, cost, per-variant tokenizing, `route()` with `dry_run`, per-decision and aggregate bindings — `router/test_router.py`, **42/42 pass** |
| `router/backends/{ollama_backend,together,dashscope}.py` | one interface, three backends; provider-metered tokens; `num_ctx` and `num_predict` set from the registry so the runtime honours what gate 3 certified; provider quirks read from `request_params` data |
| `router/probe_dashscope.py`, `router/probe_tokenizer_agreement.py` | second-provider availability, and frozen-tokenizer agreement against a provider meter (0.00% on the differenced measurement) |
| `router/verify_dashscope_route.py` | end-to-end through `route()`: **13/13 pass** on real cloud calls — cheap arm answers in one attempt (estimate −0.05% vs meter), and a real `finish_reason: length` escalates across providers on the objective trigger |
| `router/test_live_local.py` | **28/28 pass** on real local inference with a stub cloud backend — cascade, privacy of the log, and proof-of-absence verified end to end at **$0**. The count is branch-dependent by design: the suite asserts a different set of checks depending on whether the local arm held the output discipline, and on 2026-10-22 it did not, so the escalation branch ran (§13.4a) |
| `router/audit.py` | the §6.1 replay audit, the §6.2 proof-of-absence scan, and §6.3 coverage — **17/17 checks pass** on the live log. Rebuilds the registry *as it stood at each decision's timestamp* from chained history, so succession cannot make a past decision unauditable |
| `router/probe_local_arms.py` | both local arms on the three prompts that matter, verbatim answers frozen (§13.6), $0 |
| `experiments/catalog/`, `experiments/registry_probe/`, `tokenizer/` | frozen raw artifacts with sha256 manifests |

| `experiments/spine.py` | recomputes every exp2 headline number from the frozen battery **and** the frozen decision log, cross-checking two independent paths (the raw run file vs the log) — **exit 0** across four runs |
| `experiments/exp2/` | the privacy experiment: frozen battery + manifest, pre-registration (`FREEZE.md`) with a pre-run amendment, four runs, the two-key mechanism check, derived headline numbers |

Not yet built: experiments 1 and 3. `ds:capabilities` is deliberately **empty** — it is populated only from frozen runs, so until experiment 3 runs, every eligibility decision is an `allow_flagged` unmeasured pass, eligibility coverage is **0%**, and the router's choice between two $0 local arms is an arbitrary-but-deterministic tie-break with the consequence measured in §13.6. That is the honest state and it is reported, not papered over.

### 13.2 Environment findings — five, all reproducible

1. **Together returns HTTP 403 for the default Python `urllib` User-Agent.** The same request with an explicit `User-Agent` header returns 200 and the full catalog. The key was never the problem. *Hypothesis worth the monitor's attention:* the upstream efficiency experiment's `results/raw/live/runs.jsonl` contains a complete arm of `HTTP Error 403: Forbidden` entries (`Qwen3.8-Flash`, 24 of 24). Those may be a User-Agent artifact rather than a model-access problem, which would mean a frozen upstream result records an infrastructure failure as a model failure. I have not verified this — it is outside my write fence — so I am reporting it rather than acting on it.
2. **The local resolver blocks the Ollama registry.** `nslookup registry.ollama.ai` against 192.168.100.1 returns REFUSED, while 1.1.1.1 / 8.8.8.8 / 9.9.9.9 all return 104.18.16.170, and HTTPS to that address completes in 0.4 s. Consequence: the owner-approved 8B local arm cannot be pulled. The fix is a system-level DNS or `/etc/hosts` change, which needs the owner's go.
3. **The layer runs in its own venv.** `tokenizers==0.23.3` is pinned in `requirements.txt`; everything else is standard library. `.venv/` is gitignored. System Python cannot run `router/tokens.py`, and it fails closed with an explicit message rather than approximating a token count.
4. **A catalog price is not evidence of access.** Eight candidates were probed with one minimal call each (8 tokens out; **total actual spend $0.00009272**, from the provider's own meter). Only **2 of 8** were callable; the other 6 were refused with `model_not_available: "Unable to access non-serverless model"`. All 176 chat models in the catalog carry `pricing.hourly == 0`, so that field does **not** distinguish serverless from dedicated-only, and price alone cannot be used to decide callability. Consequences, all now enforced: `availability` is a required registry field whose `callable` is measured; a cloud variant with no measured availability cannot be registered; an active variant that loses availability is retired; and the audit re-checks it. The frozen artifact is `experiments/registry_probe/cloud_availability_20261022T190717Z.json`.

5. **A second provider supplies the missing cheap arm.** The owner added a DashScope (Alibaba Model Studio) key on 2026-10-22. Measured, one minimal call per candidate (`experiments/registry_probe/dashscope_availability_*.json`): the key is for the **international/Singapore** region — the mainland endpoint rejects it with "Incorrect API key provided" for every model. `qwen-flash` and `qwen-plus` are callable; `qwen2.5-7b-instruct` and `qwen-turbo-latest` return **Access denied** (not enabled on this account); `qwen-turbo` returns a request-limit error on both attempts; `qwen3-8b` does not exist. Published international prices, tier 0–256K: `qwen-flash` **$0.05/$0.40** per Mtok, `qwen-plus` $0.40/$1.20 (non-thinking) or $4.00 output (thinking); both list a 1M-token free quota. `qwen-flash` is registered as the cheap cloud arm — cheaper than anything callable on Together, with a 1M-token window. Note that the owner's first choice, `Qwen2.5-7B-Instruct`, is the *Together* identifier for a model DashScope calls `qwen2.5-7b-instruct`, and that exact model is access-denied on this account; `qwen-flash` is a hosted alias, not the open-weight 7B, so results are attributable to the alias, not to a specific weight release. That distinction is stated in the paper rather than blurred.

### 13.3 Registry provenance

Cloud prices come from `experiments/catalog/together_models_2026-10-22T182818Z.json` (269 models; 176 chat; 78 paid; $0.02–$3.50/Mtok — independently re-verified today against HANDOFF §3). Local facts come from `experiments/registry_probe/local_llama3.2_latest_20261022T183244Z.json`: 3.2B parameters, **Q4_K_M**, model max context 131,072, run at `num_ctx` 8,192, 246.4 prompt tok/s, 49.5 output tok/s, and a correct JSON-only answer on a two-row sum. `watts_nominal` 30.0 is a **declared assumption**, labelled as such in the record (`watts_source`), not a measurement (§10.3).

**Active registry: 6 variants** (after the availability migration, the DashScope addition, and the 8B local arm). Local `llama3.2:latest` (3.2B Q4_K_M, $0, 8,192-token runtime window, 49.5 output tok/s); local `llama3.1:8b-instruct-q4_K_M` (8.0B Q4_K_M, $0, same window, 24.0 output tok/s); cloud `qwen-flash` on DashScope ($0.05/$0.40, window registered at the 256K price-tier boundary); cloud `Llama-3.3-70B-Instruct-Turbo` on Together ($1.04/$1.04); cloud `Qwen3.8-2.4T-A95B` on Together in chat mode ($2.00/$6.00) and, as a separate record, in reasoning mode at an 8,192-token budget. That spans **$0 → $2.00 per Mtok input** with two local sizes at matched quantization. Eleven earlier records were retired by succession, never deleted (§9.1), and the chain records each step; re-running registration is idempotent (0 new, 0 superseded).

**Actual spend to date: $1.5250 computed from provider meters.** Breakdown, all from provider meters: $0.00009 for the eight Together availability probes; $0.01081 for the Together arm of the end-to-end cascade verification; $0.0026 for everything on DashScope (probes, the tokenizer-agreement differencing, and the `qwen-flash` attempts); $0.03855 for exp1 pilot v1; $0.00452 for pilot v2 (Together was returning 401, so only the local and DashScope arms ran — the estimate for the same roll was $0.4065); $0.27419 for pilot v3, the first roll with all five arms live; $0.0002 for the live connector checks; and $0.6160 for the three-repetition replication (`exp1-reps-v1`, 132 attempts over four connections); $0.0093 for the first 8,192-token budget probe; $0.3009 for the four-arm 8192 replication; and $0.2476 for annotation runs (five over 124 prompts, one over 188 with a second annotator) — one smoke test, one repeated after a printing bug, and two after ruleset revisions. Four of the five were iteration; the local `rules_audit.py` (no model calls) should have preceded each paid round, and that is the lesson recorded here rather than hidden. **Experiment 3 cost $0.00 in every step** — its capability rows are derived from exp1's frozen artifacts and its sweep is 144 `dry_run` decisions, and amendment B.1 was also $0 because it re-scores outputs already on disk rather than re-asking anyone. Reported this way rather than as a single number because "what it cost" and "what it would cost at list price" are different claims. Total against the owner's $10 pilot cap: **15.0%**.

### 13.4 Two measured behaviours of the local 3B arm (2026-10-22, verbatim)

Both were produced by `llama3.2:latest` (3.2B, Q4_K_M) at temperature 0 through the Ollama backend, and both changed the design conversation. They are recorded here verbatim because they are the concrete before/after examples PROJECT.md rule 4 asks for.

**(a) The structured-output hazard is real, and the objective trigger catches it.** Asked for `ONLY valid JSON: {"total": <number>}` over the amounts 120.50 and 80.00, the model returned in 0.41 s (52 tokens in, 17 out, `finish_reason: stop`):

```
{"total": 200.50}
{"total": 160.00}
```

The first object is **correct**; the second is invented. Because two concatenated objects are not valid JSON, `structural_check` is `false`, escalation trigger 1 fired, and the cascade moved to the next-cheapest feasible variant — with no confidence signal consulted. This is the literature's "correct isn't usable" result [ref 39] reproduced on this layer's own arm, and it is why §2.5 ranks machine-checkable triggers above self-reported confidence. Note the strictness is deliberate: the contract said *only* valid JSON, and a lenient check would have passed a hallucinated second total.

**(b) The privacy gate can cost the answer, not just its quality.** On a private-classified medical prompt (aspirin 100 mg, metformin 500 mg), the same model returned in 0.90 s:

> "I can't provide a summary of your medical prescription. If you have any questions or concerns about your medication, I recommend discussing them with your doctor or pharmacist."

The router behaved exactly as specified — gate 1 kept it on the device, nothing left, the decision is bound and auditable — and the user still got no answer. The cost of the hard privacy gate is therefore **availability**, not merely accuracy, and it is model-specific. This is the strongest argument so far for the owner-approved 8B local arm, which is currently blocked by the DNS fault in §13.2 finding 2.

### 13.5 Blockers: both resolved

1. **RESOLVED 2026-10-22 — the missing cheap cloud arm.** Together's cheap models are deprecated rather than gated (owner's explanation, consistent with the measured `model_not_available` refusals), so the low end of the frontier could not be reached on that provider. The owner supplied a DashScope key, and `qwen-flash` is now registered at $0.05/$0.40 per Mtok (§13.2 finding 5), verified callable and verified end to end through `route()`. Residual limitation, stated plainly: the frontier's low end is $0.05, not the $0.02 floor of the catalog, and the cheap arm is a hosted alias rather than a pinned open-weight release. **Do not** provision a dedicated Together endpoint to recover the deprecated models — that is hourly billing, a different cost model, and a decision for the owner.
2. **RESOLVED 2026-10-22 — the 8B local arm.** The DNS block on `registry.ollama.ai` cleared on its own (the local resolver now returns 104.18.17.170 and the manifest fetch returns HTTP 200), so no hosts or DNS change was needed and the HuggingFace route was not used. `llama3.1:8b-instruct-q4_K_M` was pulled — deliberately at **Q4_K_M, the same quantization as the 3B arm**, so the comparison isolates size rather than compression. Measured: 8.0B parameters, 4.9 GB, `num_ctx` 8,192 accepted, 138.6 prompt tok/s, 24.0 output tok/s (about half the 3B's 49.5, as expected). `HF_TOKEN` is present in `.env` and was **not** needed; it is loaded by name only and never printed.

Proceeding: experiment 2 (privacy, **$0**), then pilots of experiments 1 and 3 on the six registered variants within the **$10** cap, with actual cost reported before any full run.

### 13.6 The router currently selects the local model that refuses — and that is the argument for the verifiability currency

Measured with both local arms on identical prompts at temperature 0 (`experiments/registry_probe/local_arms_comparison_*.json`, $0, verbatim answers frozen):

| Prompt | 3B (`llama3.2`) | 8B (`llama3.1:8b-instruct-q4_K_M`) |
|---|---|---|
| private medical summary | **REFUSED**: "I can't provide a summary of your medical prescription…" | **ANSWERED**: "Here is a summary of your medical prescription: * Aspirin: 100mg to be taken…" |
| "ONLY valid JSON", two amounts | `{"total": 200.50}` + `{"total": 160.00}` — correct sum, then an invented one | `{"total": 120.5}` + `{"total": 80.0}` — never summed; echoed each input as a "total" |
| three-row sum (120.50+80.00+80.00) | `{"total": 280.50}` — correct, valid JSON | `{"total": 280.5}` — correct, valid JSON |

Three things follow, and the third is the important one.

1. **The 8B was worth downloading.** On private medical content the choice is not "weaker answer vs stronger answer" but "no answer vs an answer". The owner's instinct to add a middle arm was right, and §13.4(b) is now quantified rather than merely observed.
2. **Both arms break the output discipline, differently.** Neither produced a single valid JSON object on the two-amount prompt, so the structural trigger fires for both — but the 3B at least computed the right total before adding a hallucinated second one, while the 8B did not sum at all. Structured-output reliability is therefore not monotonic in model size, which is precisely why eligibility must be *measured per variant* rather than inferred from a family or a parameter count [refs 39, 40]. Small sums are fine for both (280.50 correct), so the arithmetic hazard is scale-dependent, not absolute — consistent with the upstream 0/12 on hundreds of rows.
3. **With an empty capability matrix, cost minimisation picks the model that refuses.** Both local arms are $0, so the argmin is a tie and the `id_asc` tie-break selects the 3B — the one that declines the medical prompt — while the 8B sits idle. That is not a bug; it is the documented behaviour of a cost-only objective with no measured eligibility (§3.4, §2.4). It is also the strongest available argument for this layer's central claim: **cost alone cannot express "prefer the model that will actually answer"; only a measured verifiability currency can.** Gate 2 is what fixes this, and it can only fix it once experiment 3 populates `ds:capabilities`. Until then the router's local choice is arbitrary-but-deterministic, and this spec says so rather than dressing it up.

Consequence for experiment 3, recorded now so the battery is built for it: capability rows MUST carry a measured **refusal rate** alongside accuracy, termination and schema compliance, and each prompt class's battery MUST include sensitive-content items — otherwise a variant that answers everything except medical text scores as eligible and the router keeps selecting it. Eligibility is per `(variant, prompt_class)`, so within-class content variation is averaged; that granularity is a stated limitation, not a hidden one.

### 13.7 Experiment 2 headline numbers (frozen, $0, spine exit 0)

Full pre-registration, results and per-prompt detail: `experiments/exp2/FREEZE.md`. **Six** runs over one frozen 44-prompt battery (sha256 `bd833e88…`), 264 decisions, all `dry_run`, **$0**. The numbers below are the ones `experiments/spine.py` recomputes and exits 0 on.

| Metric | Result |
|---|---|
| **M1 gate compliance** | **0 violations** in all six runs (22–23 classified-private per run). A proof about a set filter, not evidence about the world |
| **M2 end-to-end privacy** (ground-truth labels) | **0 violations / 18** labelled-private prompts, in every run |
| **M3 classifier accuracy** | **0.8182 → 0.8864 → 0.9318** across rulesets v1 → v2 → v3 (36/44 → 39/44 → 41/44), reached **by editing datasets only**, never by relaxing a safety rule |
| **M4 over-classification** (the cost of failing closed) | **7 → 5 → 3** of 44 as the rulesets gained positive public markers; the three that remain are all in the safe direction |
| **M5 under-classification** (the dangerous direction) | **1 → 0 → 0** — `p11`, a personal disciplinary letter filed as company-confidential, is fixed in ruleset v2 |
| **M6 privacy-label coverage** | 0.9545 → 0.9318 (matcher fix) → **1.0** under ruleset v3: no prompt falls through to the fail-closed default |
| **M7 proof-of-absence** | v1 **LEAKED** `p11` into the log; v2 onward store text for **0/44** decisions and their scoped scans are clean; the standing scan **still fails**, permanently (§10.11) |
| **M8 refusals** | **0** — every battery prompt fits the local window, so gate 3 was never the binding constraint here |
| **Audit** | 19 of 20 checks pass on every run; the single failure is the permanent §6.2 standing-scan leak, reported as a result, not suppressed |

Every remaining classifier error under the shipped default is **over**-classification, and the spine asserts that set equality so a future gain cannot be quietly bought with an under-classification.

Camouflage did **not** defeat the gate: all four private-behind-public prompts classified private, including a real SSN embedded in a python request, where the severity rule at rank 3 beat the `python` public marker at rank 8 (H3 confirmed).

Two defects were found and fixed, and both fixes are measured rather than assumed: the classifier-keyed logging rule (policy v2 stores no text for any class) and substring keyword matching (`nda` inside "pandas"). The matcher fix changes exactly one classification on this battery and leaves M3 **unchanged** at 0.8182 — the spine asserts both facts so the fix cannot be credited with a gain it did not produce.

### 13.8 Compute-via-code: the owner's principle, and the first result that favours the design

Owner principle (2026-10-23): where code can produce the answer, the model writes the code and
the code runs — the model never does the arithmetic. Encoded as data, not code:
`policy.objective.compute_via_code = {preferred: true, applies_to: ["compute_via_code"],
fallback: "refuse"}`, a new prompt class, and a task ruleset v2 that ranks it above
structured-output requests.

Battery v2 pairs every task: the same ground truth asked **in-head** and **via code**. Input
size is the first thing that falls out: a code request is ~170–210 tokens; the in-head version
of the same question is 1,228–24,096. Pilot v3 (`exp1-pilot-v3`, all five arms, Together key
restored by the owner) spent **$0.274189** against a $0.4065 pre-flight estimate and a $1.50
ceiling.

Measured (pilot v3 under grader **B.1** — see §13.9 before quoting any number from this section;
`experiments/spine.py` recomputes all of it and exits 0):

* **Compute-via-code clears the aggregation item that no arm clears in context.** On the 800-row
  sum, **four of five arms print the exactly right total when they write and run a program** —
  local 8B, the $0.05/Mtok cloud arm, the 70B and the flagship all at full credit — while **zero
  arms** get it right with the rows in context (every in-head attempt gets the row count right and
  the total wrong). This is the layer's central architectural claim, and it is now measured.
* **The local arms separate on code, not on price.** 8B's programs run and score 0.6667 on the
  compute class; the 3B's programs almost never run and it scores 0.0. Both cost $0 to run.
* **`ds:capabilities` now holds 10 active rows** (5 arms × 2 classes), each carrying accuracy,
  termination, schema compliance, `code_runs_rate`, the **grader** that produced it, and its own
  `thresholds_snapshot`; every stored `eligible` recomputes from that snapshot, and every stored
  accuracy equals the mean of the source rows it cites.
* **Policy-only rerouting, at $0.** Same six compute items, same frozen measurements, only policy
  fields varied (`experiments/exp3/results/derived/sweep_exp3-sweep-v3.json`, 24 points, 144
  `planned` decisions, no model call): the winner moves across four different arms as the floors
  move — 3B ($0, accuracy 0.0) → 8B ($0, 0.6667) → 70B ($0.001054, 0.8333) → flagship
  ($0.002018, 1.0).
* **The two thresholds are not equal levers, and the expensive one is the weak metric.** Releasing
  the accuracy floor alone (schema ≥ 0.9 kept) still lands on the flagship at $0.002018; releasing
  the schema floor alone (accuracy ≥ 0.8 kept) lands on the 70B at **$0.001054 — half the price for
  measured accuracy 0.8333 instead of 1.0**. `min_schema_compliance` is therefore doing the pricing,
  while for code items "schema compliance" only means *the program printed a parseable JSON
  object* — key names and value types are not checked (§13.9 limit 2). A deployment should either
  tighten that definition or lower that floor; as shipped it pays 2× for a guarantee weaker than its
  name suggests.
* **Price of the floor, item by item:** the shipped defaults cost **40.0× what the cheapest
  *measured* cloud arm would have cost on the same six items** (uniform 40.0× across all six, since
  it is a pure price ratio: $0.05/$0.40 vs $2.00/$6.00 per Mtok). Against the no-floor winner no
  ratio is defined — that arm is local and declared $0 by the user, which is a declaration, not a
  measured zero (§3.2, RC-3).
* **The earlier fall-through hazard is now closed, and its replacement is subtler.** With 3 of 6
  arms measured, the shipped defaults excluded everything and `allow_flagged` let an *unmeasured*
  arm win. At 5 of 6 arms measured no sweep point ever chose an unmeasured arm — but one arm
  (`Qwen3.8-2.4T-A95B-reasoning`) still has no row at all, so `missing_measurement: refuse` removes
  it from the candidate set while `allow_flagged` admits it, and that difference changes **0 of 12**
  routing outcomes because it is never the cheapest. Mode is a candidate-set knob here, not a
  decision knob; the spine asserts both halves of that sentence rather than narrating one.
* **A methodological trap, recorded because I fell into it.** The first sweep varied `min_accuracy`
  alone and returned a perfectly flat curve. Eligibility is a conjunction, and
  `min_schema_compliance` was the binding constraint the whole time. The sweep is now two-axis and
  prints which constraint binds per arm. The 16 policy records from the confounded first pass are
  superseded, not deleted.
* Three rolls of the sweep are kept (`exp3-sweep`, `-v2`, `-v3`) because they rest the same 24
  policy records against three different measurement states; the policy is the swept variable, so a
  new roll must not mint new policy records for identical thresholds.

### 13.9 Amendment B.1 — a defect in Layer 5's own instrument, found by the spine

The spine, not the experiment, found this. Four battery items tell the model to print
`{"n_rows": …, "total_amount": …}` and store ground truth as `{"n_rows": …, "total": …}`; the
scorer matches fields **by name**, so a program that ran and printed the exactly correct sum lost
the total field on every arm. Reported in full at
`experiments/exp1/FREEZE.md` §6; the correction is `experiments/exp1/regrade.py`.

Why it mattered: the defect depressed every `compute_via_code` accuracy by 0.17–0.33 and made the
architecture's headline result invisible — under the buggy grader *no* arm appeared to clear the
800-row total by any route, which read as support for the upstream "totals must not come from the
model" conclusion.

How it was corrected, and why that is legitimate: the prompt text never changed, so the models were
asked the same question and their outputs are already on disk. `regrade.py` parses the output shape
each prompt mandates (mechanically, for all 12 items — it found exactly these 4 and no others),
re-scores the frozen `program_stdout`, and writes a **sidecar**
(`experiments/exp1/results/derived/arms_exp1-pilot-v3-regraded.jsonl`). The frozen raw roll is
untouched. Re-summing the corpus independently confirms the ground truth (800 rows / 361,572.40).
A fresh roll would have spent money measuring sampling noise around a scoring bug, so spend stays
**$0** for this amendment.

Three limits are part of the record, not footnotes:
1. The two affected **in-head** items cannot be regraded — `run_v2.py` stored their score but not
   their reply text. Their capability rows keep the old grader and now say so in
   `measured.grader`; `run_v2.py` preserves reply text for future rolls.
2. `schema_compliance` for code items is `isinstance(parsed, dict)` — it does not check key names,
   which is why this defect never surfaced there. The name oversells the guarantee (§13.8).
3. Rows already written under the old grader are **superseded, not deleted** (§9.1), so any
   decision that pinned them stays auditable via `registry_at()` (§6.4).


## 14. Delta: required_changes.md (RC-1 … RC-5) — binding, 2026-10-25

`required_changes.md` (owner, via monitor, 2026-10-23) supersedes the sections named below.
The old text is **kept, not rewritten**, per that file's own instruction: §1.2, §2.1, §3.2,
§3.4, §5, §12.6 and §13.3/§13.4 stand as the record of what was believed then. This section
is what is true now. Where the two disagree, this section wins.

### 14.1 What changed

| RC | Supersedes | New behaviour |
|---|---|---|
| RC-1 | §1.2 (agent-curated `ds:models`) | `ds:models` is the **user's connect list**: `label`, `connector`, `base_url`, `model`, `auth.key_env`, `locus`, `privacy_class`, `price`, `context_window`. No catalog fetch, no suggestions, no provider enumeration in the product path. Secrets are env-var **names** only. |
| RC-2 | §5 (three provider backends) | Exactly two wire formats: `openai_compatible` and `ollama`. `together.py`, `dashscope.py`, `ollama_backend.py`, `http.py` are gone from `router/`. Provider quirks are data (`request_params`), not branches. |
| RC-3 | §3.2, §12.6 | The local shadow price, latency price and energy tariff are **removed**. Objective is `min_declared_cost`; unpriced connections rank last and say so. Local wins by being declared $0. The frontier stays in experiment 1 with local at $0. |
| RC-4 | §2 (three gates, no failure model) | Four gates (`privacy → availability → verifiability → context_fit`), plus: per-call connect/total timeouts, ≤2 retries on transient classes only, per-connection circuit breaker (3 failures / 60 s), measured availability, fallback inside the feasible set with the whole chain in **one** decision, file-locked appends, typed malformed responses, typed refusal when everything fails, and boot that tolerates a broken entry. |
| RC-5 | §5, §11 | Any endpoint-speaking runtime is routable through the generic connector; GUI-only apps are out of scope for this layer **and** the shell. Subscription ≠ API entitlement, stated in `CONNECT.md` and here. |

### 14.2 Resolutions and pushback (requested by §8.6)

**D-1 — `privacy_class` naming.** The RC example uses `P0`. This layer's closed enum is
`public | confidential | private`, shared with Layers 1–4 and pinned in five schemas and
several hundred historical decisions. Implementing a second vocabulary would fork the
privacy model for a naming preference, so `P0/P1/P2` are accepted as **input aliases**
(`private/confidential/public`) and normalised on read. A real P-scale needs a monitor
ruling, not a layer-local decision.

**D-2 — migrated prices are carried, not verified.** The six connections created by
`apply_rc_migration.py` carry the prices that came from the pre-RC catalogs, marked
`source: "user_declared"` because from now on the list is the user's. They are not
independently re-checked, and the user should confirm them. Stated so no one reads that
field as a measurement.

**D-3 — the audit's registry checks changed shape.** "Every active cloud variant is
measurably callable" cannot survive RC-1: a connection the user just added has never been
called, and RC-4.4 says availability is established by a real call. The check is replaced by
four others (price guard; user-declaration provenance; **no secret material**; routable
shape), and measured availability moves into `health_snapshot` on every decision plus the
breaker. The check **count** stays at 17 for a no-needle run, which is what §7.10 asserts.

**D-4 — capabilities follow the model, not the row.** RC-1 lets a connection record be
replaced while the model behind it is unchanged. `ds:capabilities` therefore attaches by
`capability_key` (the endpoint's model id), and rows written before RC-1 are resolved
through the old record's `upstream_id`. The output budget a row was measured at is recorded
but is **not required to match** — refusing evidence because a budget moved would discard
the only measurement available; a mismatch appears in the gate notes instead.

**D-5 — tokenisers are optional, and the substitute is declared.** A user connecting LM
Studio cannot be asked to choose a BPE file. When a connection declares no `tokenizer_ref`,
gate 3 uses a conservative upper bound (3 characters per token) and the decision record says
`est_tokens_method: char_div_3_upper_bound`. When one **is** declared, a missing or altered
file still fails closed, as §3.3 requires.

**D-6 — an undeclared RAM budget no longer excludes.** §2.3's RAM sub-check excluded local
connections with no `local_resources`, which under RC-1 is every user connection. That would
make the shipped policy refuse everything. Undeclared RAM is now a recorded note
(`ram_undeclared`), not an exclusion; a declared budget that does not fit still excludes.

**D-7 — check 7 passes, with one substitution and one live lesson.** The same
`openai_compatible` class answered from a real local endpoint (Ollama `/v1`: 51 prompt
tokens, 6 completion, provider meter, $0) and from a real cloud provider (Together
`zai-org/GLM-5.3-Flash`: metered **$0.0001962** (38 in, 381 out)). Two things had to be
substituted, and both are worth recording rather than hiding:

* the 70B this layer measured in October (`meta-llama/Llama-3.3-70B-Instruct-Turbo`) is **no
  longer in the provider's catalog** — the connection migrated from the pre-RC registry now
  points at a retired model and fails with a typed `model_not_found`;
* the org's Together account **blocks models served via third parties** (HTTP 403,
  "third-party data sharing not enabled"), which the router surfaced as a typed `auth`
  failure without retrying or crashing. A first-party model was used instead.

DashScope's key is still invalid (401), so that connection is currently dead as well. The
"desktop app" leg remains Ollama's `/v1` surface: no GUI app is installed here and installing
one needs the owner's go-ahead (RC-5.1). None of this is code; all of it is the connect list
telling the truth about the world.
**D-8 — nothing was destroyed.** Deleted from `router/` but kept for provenance under
`experiments/registry_tools/superseded/`: `backends/together.py`, `backends/dashscope.py`,
`backends/ollama_backend.py`, `backends/http.py`, `smoke_cloud.py`,
`verify_dashscope_route.py`, `test_live_local.py`, `probe_dashscope.py`,
`probe_local_arms.py`, `probe_tokenizer_agreement.py`. Demoted (still runnable, no longer on
the product path): `fetch_catalog.py`, `probe_cloud.py`, `probe_local.py`,
`register_models.py` → `experiments/registry_tools/`.

### 14.3 The three connection kinds, and only these

| Kind | Example | Connector |
|---|---|---|
| Local runtime with an endpoint | Ollama, LM Studio, Jan, GPT4All, llama.cpp server, vLLM | `openai_compatible` (Ollama: native) |
| API provider | OpenAI, Moonshot/Kimi, Together, DashScope, OpenRouter, Groq | `openai_compatible` |
| Closed GUI app | ChatGPT desktop, Kimi Work | **none — not a routing target** |

A subscription to a closed desktop app is not an API entitlement. ChatGPT Plus cannot be
routed; the user who wants that model connects the OpenAI API with a separately billed key.
Kimi Work maps to the Moonshot/Kimi platform API, which the generic connector already covers.
Said here, in `CONNECT.md`, and in the paper so the expectation is set before the
disappointment.

### 14.4 Acceptance evidence

`router/test_acceptance_rc.py` — checks 1–6 and 8–10 run offline against a real local HTTP
server with switchable failure behaviour; 9/9 pass. `router/acceptance_live.py` — check 7.
Results and the exact commands are in `experiments/spine.py`'s run log and the report that
accompanies this delta.

---

## 15. Delta: owner override on privacy and on the model list (2026-10-25)

Owner, verbatim: *"the model list should be dynamically updated because each user has their
own providers. the user is responsible of their data privacy with their providers. not ours
to judge or block."*

This supersedes two MUSTs in `required_changes.md`: §6 ("the privacy gate stays a hard
set-filter") and RC-1.5 ("no runtime catalog fetching, provider enumeration"). Both
superseded clauses remain readable in §14; this section is what is true now.

### 15.1 Privacy: the gate enforces the user's rule, not ours

**P-1.** Policy **v8** is the default. `privacy.allowed_loci` permits local *and* cloud for
every class; `privacy.warn_on_cloud_for = [private, confidential]`. v7 — local-only for
private and confidential — stays active as the **strict profile** and is what
`test_acceptance_rc.py` check 6 pins, so the mechanism remains demonstrated rather than
merely available.

**P-2.** No request is refused because *this layer* judges the destination. A sensitive
request that reaches a cloud endpoint carries `privacy_warning` in the decision record and in
the response. That is the whole obligation: say it loudly, on the record, and get out of the
way.

**P-3.** Two declarations must agree, and **the stricter one wins** — this is not a second
gate, it is what "declared" has to mean:

| Declaration | Where | Set by |
|---|---|---|
| may private content use a cloud endpoint at all? | `policy.privacy.allowed_loci` | the policy owner (once) |
| may *this endpoint* see private content? | `connection.privacy_class` | the user, per connection |

A newly added cloud connection defaults to `privacy_class: public`; raising it is a
deliberate act (`connect.py --set-privacy "<label>" --privacy-class private`). Connecting a
provider is not consent to send it everything.

**P-4 — what this costs the paper, stated rather than hidden.** "0 private requests reached a
cloud endpoint in 18 attempts" is a property of the **strict profile**, not of the
architecture. The architectural claim that survives is narrower and still measurable:
*no decision ever routes outside the policy in force, and every sensitive cloud route carries
a recorded warning.* Both are now what the audit checks (§6.1.7), on every run, for the policy
that run used.

### 15.2 The model list: refreshed by the user, never by the router

**M-1.** `router/connect.py` is the update mechanism — `--list`, `--add`, `--status`,
`--retire-broken`, `--set-privacy`. Listing and status call the provider; **routing still
never does**, so a decision remains a pure function of the list as it stood.

**M-2.** `--status` makes a **real one-token call** per connection, because being listed is
not the same as being callable. Measured on 2026-10-25: Together still lists
`meta-llama/Llama-3.3-70B-Instruct-Turbo` and rejects it with *"Unable to access non-serverless
model"*, and blocks third-party-hosted models outright (HTTP 403). A listing-based check would
have called that connection healthy.

**M-3.** Broken connections are **shown with the reason, never removed silently**;
`--retire-broken` retires them by succession only when the user asks.

### 15.3 Status after the override

Ten acceptance checks pass (11 assertions including the user-choice case): failure modes 1–6
and 8–10 unchanged, check 7 passes against Ollama `/v1` and Together `zai-org/GLM-5.3-Flash`
(metered $0.0001962 on the final run, 38 in / 381 out — the model's own verbosity sets the bill), and the new check 11 proves a private request may reach a cloud endpoint
with a warning and still audit clean. Spine exits 0, 226 checks, experiment 2 untouched.

---
---
*Companion artifacts: design brief (`brief.md`, owner-confirmed 2026-10-22), systematic review (`litreview/review.md`, 45 verified works), handoff (`HANDOFF.md`). Reference numbers `[n]` are that review's. On freeze, this spec becomes the contract Layer 6 codes against.*

## 16. Known open defects and next actions (2026-10-25, end of build)

Recorded so the next session starts from facts rather than from the happy path.

1. ~~Two under-classifications in the shipped privacy ruleset.~~ **FIXED in ruleset v5 / policy
   v11 (2026-10-25).** The 80-item expansion found two items the original 44 could not reach
   (*"my loan repayment went up by 90…"* and *"my performance review says I need improvement"*); a
   local re-audit after fixing those found three more of the same kind (a first-person employee
   record filed as confidential, plus two business prompts that matched no rule and fell to the
   fail-closed default). Ruleset v5 fixes all five. `experiments/exp2/rules_audit.py` now reports
   **0 under-classifications and 3 over-classifications on 124 items**, and the original 44 still
   score **0.9318**, so every published experiment-2 number is unchanged. See §17.
2. **The capability matrix is single-budget.** Every row is measured at 2,048 output tokens, and
   the same cheap arm scores 0.22 at that budget and 1.00 at 8,192. "Only the flagship clears the
   floors" is true at 2,048 and says nothing about the models in general. A second budget would
   roughly double the matrix for about $0.6.
3. **The paper has not been compiled.** No LaTeX toolchain on this machine; the source passes a
   structural check (braces, environments, escapes) but not a build. Its bibliography is marked
   unreconciled with `litreview/review.md` and must not be submitted as-is.
4. **The second annotator is a model.** It shows the labelling task is well-posed (kappa 0.96
   against the author) but does not substitute for a human second annotator.
5. ~~One live desktop-app leg unproven.~~ **RESOLVED 2026-10-26 (owner approved install).** LM Studio
   was installed via Homebrew; its GUI would not launch headless (first-run dialogs need a human),
   so its own bundled llama.cpp server binary (2.54.0, Apple Metal arm64) was run directly at
   `127.0.0.1:1234/v1` — the desktop-server address and protocol — serving Qwen2.5-0.5B-GGUF. The
   generic `openai_compatible` connector routed, answered, and metered the call ($0);
   `experiments/desktop_app_check/check7_lmstudio.py` + `evidence.json`.

## 17. Ruleset v5 and policy v11 (2026-10-25)

Ruleset v5 adds five first-person phrases and three business phrases to the privacy rules; policy
v11 points at it and is the default. Nothing else changes: the gates, thresholds and classifier
structure are untouched, and policy v10 remains active but is no longer the default.

| Before (v3) | After (v5) | Direction |
|---|---|---|
| "my loan repayment …" → public (no match) | private | danger fixed |
| "my performance review …" → confidential | private | danger fixed |
| "my employee record … appeal hearing" → confidential | private | danger fixed |
| "onboarding notes for our new client" → private by fail-closed default | confidential | over-classification removed |
| "this client's data export" → private by fail-closed default | confidential | over-classification removed |

Measured with `experiments/exp2/rules_audit.py`: 124 items, accuracy **0.9758**, **0 under**,
3 over (a Python validator for card numbers, hospital-record schemas, a template email — all general
questions about sensitive topics, kept deliberately). The original 44 score **0.9318**, identical
to `exp2-v5`, so the frozen experiment stands.

**The caveat that must travel with these numbers:** the ruleset was revised against this 124-item
set, so 1.00 on the 80 new items is a regression test, not held-out evidence. What is not circular
is the agreement with the independent annotator, which never saw the rules: 0.967, kappa 0.945
(§5.x of the paper). A held-out battery is the missing piece.

## 18. Held-out evaluation — the classifier does not generalise (2026-10-26, revised 2026-10-26)

A 64-item held-out battery (`experiments/exp2/battery/prompts_heldout.jsonl`) was written **after**
ruleset v5 was frozen and never used for tuning. `experiments/exp2/rules_audit.py` scores it:

| Measure | Tuning set (124) | Held-out (64) |
|---|---|---|
| accuracy vs author | 0.9758 | **0.6250** |
| under-classifications | 0 | **3** (h02, c09, a02) |
| over-classifications | 3 | 21 |
| prompts matching no rule | 7 | **37** (58%) |

**This is the most important negative result in the layer.** The ruleset's 0.98 on the tuning set was
partly the ruleset being revised against the items it was scored on; out of sample it loses three
prompts in eight, and more than half the held-out prompts match no rule at all (the fail-closed
default turns most of those into over-classification, which is the safe direction, but three go the
other way).

What is *not* affected: the gate obeys whatever class it is given, exactly and reproducibly; the
routing, cost and audit results depend on the class as an input, not on how it was produced. What
*is* affected: any product claim that the router classifies privacy reliably. It does not, and no
paper or README should imply otherwise.

Two independent model annotators (Qwen3.8 and DeepSeek-V4.1, different families) reproduce the
author's labels at 0.984 ($\kappa$ 0.974), and DeepSeek agrees with the author on **every** held-out
item — so the labels are not the weak link; the rule-based classifier is. The honest next step is a
learned classifier with the same audit trail, evaluated on a **third** battery that none of this
work has touched.

### 18.1 Ruleset v6: a structural guard, not more phrases

Ruleset v6 adds a **public-match guard** (`rules.public_guard`): when a public rule wins, the
classifier checks for a personal or business domain term and demotes the verdict — to `private`
for personal domains, `confidential` for business ones. The demotion is recorded on the decision
as `classifier_guard: {demoted_from, to, term}` so the reason is auditable.

Why a guard rather than more keywords: all three held-out failures had the same shape — a weak
public marker ("explain", "summarise") winning because the sensitive domain noun was outside the
private vocabulary. Adding those three phrases fixes three items; the guard fixes the shape. It can
only move a verdict toward the restrictive side, so it cannot create a new under-classification.

**Measured effect** (`experiments/exp2/rules_audit.py`, policy v12):

| Set | n | accuracy | under | over | matched no rule |
|---|---|---|---|---|---|
| tuning (44 + 80) | 124 | 0.9758 | 0 | 3 | 7 |
| battery A (used to design the guard) | 64 | 0.6719 (was 0.6250) | **0** (was 3) | 21 | 37 |
| **battery B (fresh, never tuned on)** | 72 | **0.5417** | **7** | 26 | 46 (64%) |

The guard removed the dangerous direction on battery A, as designed. Battery B — written after the
guard, in domains the ruleset had never seen (construction, non-profits, utilities, retail,
recruitment, medical billing, legal aid, school administration, insurance, logistics) — shows the
underlying problem is not fixable by phrase engineering: **64% of prompts match no rule at all**,
and seven are under-classified because a public marker still wins where no guard term exists
("builder's quote", "building survey", "electricity bill", "broadband contract", "placement
contract", "policy renewal quote", "driver's licence check").

**Conclusion, recorded rather than papered over:** a hand-written phrase ruleset is a reasonable
prototype and an inadequate production classifier. The next step is a learned classifier or a
model-backed fallback, evaluated on a battery that does not yet exist.

## 19. The classifier: measured options, and why the fix is not free (2026-10-26)

`rule v6` adds a **public guard**: a public match is demoted when the text names a personal or
business domain term (49 / 38 terms). It removed all three under-classifications on battery A
(0.625 → 0.672, under 3 → 0). On battery B — 72 fresh prompts in domains the rules had never seen —
the guards are not enough, and the honest numbers are:

| Classifier | accuracy | under (dangerous) | over | cost per request |
|---|---|---|---|---|
| rules v6 (with guard) | 0.542 | 7 | 26 | none |
| rules + "two public markers" test | 0.500 | 3 | 33 | none |
| rules + local-8B verifier | 0.556 | 6 | 26 | 2–5 s local latency |
| rules + frontier verifier (Qwen3.8) | **0.931** | **3** | 2 | one cloud call that reads the prompt |

**The mechanism.** `router/verifier.py` + policy field `classifier.verify_public_with`:
* only a PUBLIC verdict is ever checked (the only verdict that can under-classify);
* a stricter verdict wins; a **failed** verification demotes to `confidential`, never back to public;
* the verdict, whether it changed the class, and its cost are recorded on the decision
  (`classifier_verification`), so the second opinion is auditable like everything else;
* **default off** (`verify_public_with` absent). Enabling it is a privacy decision — the verifier
  must read the prompt, so a cloud verifier receives every public-looking prompt, *including the
  ones the rules got wrong*. That is the owner's call, not a default this layer should pick.

**Acceptance:** check 12 (`router/test_acceptance_rc.py`) proves both behaviours — a private verdict
reclassifies, an unparsable verdict demotes to confidential.

**The residual three.** On battery B, after frontier verification the remaining under-classified
items are ones where the independent annotator agrees with the classifier against our own label
(household contract questions). That is label ambiguity, not a safety failure — and it is the
reason a fourth battery, written after any classifier change, remains the only honest test.

## 20. Classifier mode: `trust_user` is the default (2026-10-26, owner decision)

Owner, verbatim: *"i don't understand. why do we still have a policy? the user does what they
want."* and *"set classifier mode to trust_user."*

The router keeps a policy because it must answer "which model, in what order, at what quality" on
every request; that is preference storage, not permission. But **sensitivity classification is now
optional and off by default**:

| `classifier.mode` | Behaviour | When to use |
|---|---|---|
| **`trust_user`** (default, policy v13) | no text analysis, every request routable, `privacy_class` recorded as `public` **by construction**, `classifier_mode` records that no classification happened | the owner's position: the user connects the providers and owns the consequence |
| `rules` | classifier + privacy gate + `privacy_warning` on sensitive cloud routes | anyone who wants the stricter profile, and all of experiment 2 |

What changes in the record: `classifier_mode` is a new field; under `trust_user` the class is
`public` and the audit knows it was not computed. What does **not** change: the gate still runs, but
with every request public it excludes nothing; the connections' own declared `privacy_class` still
applies, so a connection the user marked `public`-only is never given `private` content — that is
the user's declaration about that endpoint, not a judgement about the user.

Why this is also the engineering answer: under `rules`, the classifier scores **0.542** on a fresh
battery with seven dangerous errors, and the only measured fix (a frontier verifier) reaches 0.931
by reading every public-looking prompt. Shipping the weakest component as a default gate is worse
than not gating, so the default is the simple mode and the strict one is opt-in.

Experiment 2's numbers are unchanged and remain valid **for the rules profile**; the paper says so
where they appear.

---

## 21. Freeze: v1.1 (2026-10-26)

Frozen per `instructions_26_oct_2026.md`. This is the simplified system: user-owned connect list
(RC-1), one generic OpenAI-compatible connector plus Ollama (RC-2), no shadow price or latency
price — `objective.type = min_declared_cost`, local wins by being declared $0 (RC-3), the RC-4
failure model, and the three connection kinds with GUI-only apps out of scope (RC-5). Sections
§12.6 and the pre-RC §3.2/§12.6 reasoning are kept as marked-superseded record only.

**Freeze-state evidence (recomputed 2026-10-26):** `router/test_store.py` OK;
`router/test_acceptance_rc.py` exit 0 (12 checks, offline legs; check 7 live in
`router/acceptance_live.py`); `router/audit.py` **17/17** on the live log (828 decisions, binding
coverage 1.0); `experiments/spine.py` exit 0 — every headline number recomputed from frozen
artifacts. No dataset was dropped in the simplification, so no identity prefix is retired:
`mdl_*`, `cap_*`, `pol_*`, `rul_*`, `dec_*`, `req_*` all stand and are offered to the monitor for
registration in `INTERFACES.md`. Spec sha256 at freeze is recorded in `FREEZE-v1.1.md`.
