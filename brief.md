# Layer 5 — Model Router · Design Brief

**Working title:** *Where Should This Query Run? Cost-, Privacy-, and Verifiability-Aware Model Routing for Personal AI Systems*
**Status:** v0.2 — **confirmed by owner 2026-10-22.** Prototype and spec work may begin.
**Owner confirmation (2026-10-22), item by item:**
- (a) three-currency policy algebra — **confirmed as scoped**, standard gate order (privacy → verifiability → context_fit), cost optimised inside the feasible set. Owner note: the gate *order* stays fixed for now, but the **parameters become user-configurable later** (thresholds, privacy-class → locus mapping, escalation depth). Design must therefore keep every tunable in `ds:policy` data, never in code.
- (b) deterministic gates, minimal learned surface — **confirmed.** Fixed auditable rules for now, to keep the system simple and explainable. Learned scoring stays an evaluated-later ablation.
- (c) the three frozen experiments — **confirmed**, with an owner caveat: **this layer is exploratory.** Batteries are frozen before each run for provenance, but a frozen result that changes the design is expected to feed back into the next policy version rather than be defended. Report what is found, including results that argue against the design.
- (d) Together + Ollama backends — **confirmed.** Together AI key supplied by owner 2026-10-22 (`.env`, gitignored precondition); Ollama install in progress.
**Builds on:** Qafiya spec v1.1 (frozen); the efficiency-experiment harness (`qafiyah/efficiency-experiment/`) as measurement starting point; the defect-eval battery (`qafiyah/defect-eval/`) for the verifiability experiments.
**Literature basis:** `litreview/review.md` — 45 verified works; the gap this design fills is stated there (§4) and not re-argued here.

---

## 1. What this layer is

Per request, decide which model answers — local or cloud, large or small — under an **explicit policy over three currencies**:

| Currency | Nature | Enforcement |
|---|---|---|
| **Privacy** | Does this data leave the device? | **Hard gate.** Deterministic. Private-classified prompts never reach a cloud model. Compliance is measured, not asserted (target: 100%). |
| **Verifiability** | Can this model do this prompt class *under the binding discipline*? | **Eligibility constraint.** Models with measured, frozen failures on a prompt class (e.g. reasoning-mode models that never terminate on tabular prompts) are removed from the feasible set before cost is considered. |
| **Cost** | Tokens/dollars (and latency, locally). | **Soft objective.** Minimised *within* the feasible set only. Learned or heuristic — never allowed to override a gate. |

The design principle, straight from the literature review: existing routers optimise one soft objective (cost subject to quality) over a homogeneous model zoo. This router's contribution is the **policy algebra** — gates first, optimisation inside the feasible set — plus the fact that the policy and every decision are Qafiya-bound data.

## 2. What the literature dictates about the design

Findings from `litreview/review.md` that constrain this brief:

1. **"Most of the LLM routing gap is task type"** [ref 17]. A router needs coarse task classification more than a learned scorer. Our router starts with a deterministic task-type + difficulty heuristic; learned scoring is an optional, evaluated-later refinement — not the baseline.
2. **Calibrated uncertainty is the dominant escalation signal** [refs 20, 36, 45]. Where we escalate small→large, we use the small model's self-reported confidence/agreement, a signal the Qafiya live runs already log.
3. **Supervision can be cheap** [ref 25]. Router labels come from frozen battery results, not a new annotation campaign.
4. **Routing metadata leaks** [ref 33]. The decision log must not store prompt content for private-classified requests — only identifiers, hashes, and policy fields. This is a design requirement, not an afterthought.
5. **Adversarial escalation exists** [refs 42, 43]. A deterministic-gate router is partially attack-resistant by construction (gates cannot be sweet-talked); the soft cost optimiser is the attack surface, and the paper says so.
6. **Structured-output failure is measurable** [refs 39, 40]. The verifiability currency is fed by *measured* per-model, per-prompt-class results — the defect battery — not by assumptions about model families.

## 3. The routing policy as data

The policy is a **Qafiya dataset**, not code. Three frozen datasets:

### 3.1 `ds:models` — the model registry

One record per available model:

```jsonc
{
  "id": "r_qwen3-14b-local",
  "name": "Qwen3-14B-Q4",
  "backend": "ollama",                 // ollama | together | ...
  "locus": "local",                    // local | cloud
  "privacy_class": "P0",               // P0 = may see anything; P1 = non-private only; P2 = public only
  "price": { "input_per_mtok": 0.0, "output_per_mtok": 0.0 },   // USD; local = 0 + measured watts/latency
  "context_window": 32768,
  "modes": ["chat"],                   // chat | reasoning — reasoning mode is a separate record if priced/behaves differently
  "capabilities_ref": "ds:capabilities",   // binding to measured capability rows
  "registered_at": "…", "spec_version": "1.1"
}
```

Prices and limits are records with provenance (source URL, retrieval date) — when Together changes a price, a new record supersedes the old, and every historical routing decision remains explainable against the price that was frozen *at decision time*.

### 3.2 `ds:capabilities` — measured model × prompt-class fitness

One record per (model, prompt-class) pair, populated **only from frozen experiment runs**:

```jsonc
{
  "id": "r_cap_0017",
  "model_ref": "r_qwen3-14b-local",
  "prompt_class": "tabular_summary",   // closed enum, aligned with the defect battery
  "measured": { "accuracy": 0.83, "termination_rate": 1.0,
                "schema_compliance": 0.97, "source_run": "ds:run_2026_11_07" },
  "eligible": true,                    // false ⇒ hard exclusion from that prompt class
  "binding": "b_…"                     // every number traces to raw artifacts (R1–R6)
}
```

`eligible: false` is the verifiability gate's entire input: e.g. the reasoning-mode records on tabular prompts get `eligible: false` from the Qafiya live-run measurements, quantifying the reasoning-model hazard as a routing constraint — one of the three frozen experiments.

### 3.3 `ds:policy` — the decision policy itself

```jsonc
{
  "id": "r_policy_v1",
  "privacy_classes": { "private": ["local"], "confidential": ["local"], "public": ["local","cloud"] },
  "gates_order": ["privacy", "verifiability", "context_fit"],
  "objective": { "type": "cost_then_quality", "escalation": "confidence_threshold",
                 "confidence_min": 0.7, "max_cascade_depth": 2 },
  "task_classifier": { "type": "deterministic", "rules_ref": "r_taskrules_v1" },
  "logging": { "store_prompt_content": "never_for_private" }
}
```

The policy is versioned data. An experiment = (task battery, policy version, model registry snapshot) → frozen results. Ablating the policy is editing a dataset, not code.

## 4. The decision procedure

Deterministic gates first; optimisation strictly inside the feasible set. Pseudocode:

```
route(request):
  cls   = classify_privacy(request)          # private | confidential | public  (HARD GATE)
  ttype = classify_task(request)             # prompt class, deterministic rules v1
  F     = { m ∈ ds:models : locus(m) ∈ policy.privacy_classes[cls] }      # gate 1: privacy
  F     = { m ∈ F : eligible(m, ttype) }                                   # gate 2: verifiability
  F     = { m ∈ F : context_window(m) ≥ tokens(request) }                  # gate 3: fit
  if F = ∅: return REFUSE(bound record: reason = gate that emptied F)      # refusal is a result, logged
  c     = argmin_{m ∈ F} expected_cost(m)                                  # soft: cheapest first
  ans, conf = run(c, request)
  if conf < policy.confidence_min and depth < max_cascade_depth:
      escalate to next-cheapest eligible model                             # calibrated-uncertainty cascade
  log(bound_decision_record)                                               # Qafiya binding; see §5
```

Properties the paper can claim:

- **Privacy compliance is decidable and auditable**: gate 1 is a set filter over frozen data; the experiment replays the decision log and counts violations (target 0).
- **Verifiability is enforced before economics**: a disqualified model is absent from `F`, so no price can resurrect it.
- **Refusal is a first-class outcome**: when no model is eligible, the router refuses with a logged reason — the honest-architecture rule applied to routing.
- **The learnable surface is minimal**: only the confidence threshold and escalation order are tunable; both are policy data, both ablatable.

## 5. The prototype

- **Fronts:** Together API (cloud models) + one local backend (Ollama, Apple Silicon). Both behind one interface so the router sees only `ds:models` records.
- **Every routing decision is a bound record** (Qafiya binding, `kind: "claim"`): which gates ran, which models survived each gate, the policy version, the registry snapshot id, the cost estimate, the confidence, the final choice, the measured outcome. For private-classified requests the record stores a content hash and metadata, never the prompt text [ref 33].
- **The spine script** recomputes every headline number (frontier points, compliance rate, hazard rates) from raw decision logs + frozen batteries. If the spine fails, the number does not go in the paper.
- Deliberately *not* built: a learned router baseline is implemented only if the deterministic baseline's frontier leaves a measurable gap worth closing (the literature predicts it won't for coarse task types).

## 6. Frozen experiments

All batteries frozen before runs; runs logged as bound records; spine script recomputes everything.

1. **Cost–quality frontier.** Frozen task battery (reusing the efficiency-experiment corpus + prompt classes). Arms: always-big (strongest cloud), always-small (local only), and routed (policy v1). Report the frontier and the crossover points — where routing stops paying. Baseline expectation from the literature: large cost reduction at small quality cost [refs 1, 3, 4, 8]; our contribution is reporting it *under the gates*, i.e. with private prompts forced local.
2. **Privacy policy compliance.** Frozen prompt set with labelled privacy classes (private/confidential/public), including adversarially-phrased ones ("summarise my medical bill" embedded in a request that looks public). Metric: fraction of private-classified prompts whose routed locus is local — target 100%, reported as a measured number with a bound record per decision, Layer-1 style. Any violation is a reported defect, not a filtered case.
3. **Verifiability interaction.** Run the Qafiya defect battery against the routed models; measure per-(model, prompt-class) accuracy/termination/schema-compliance; freeze into `ds:capabilities`; then show routing-with-eligibility outperforms routing-without on verifiable correctness, and quantify the reasoning-model hazard (e.g. termination rate of reasoning-mode models on tabular prompts) as a routing constraint. Negative results — classes where no eligible model exists — are reported as refusals, and the refusal rate is a headline number.

## 7. Honesty section — what this router provably cannot do

Stated in the paper, measured where possible:

1. **Privacy classification quality bounds everything.** The gate is only as good as `classify_privacy`; a misclassified private prompt routed to the cloud is a system failure the router cannot catch. We measure classifier error on the labelled set and report it separately from gate compliance.
2. **No protection against a malicious or compromised local model** — the threat model covers data leaving the device, not local behaviour.
3. **Cost estimates are estimates**: cloud token counts are known post-hoc; the frontier is reported on *measured* cost, with estimate-vs-actual error stated.
4. **Eligibility is per frozen measurement**: a model update (silent provider change) can invalidate a capability row; the registry's provenance fields make this detectable, not preventable.
5. **The soft optimiser is adversarially steerable** [refs 42, 43]; the gates are not. Worst-case adversarial cost inflation is bounded by the most expensive eligible model, and we state that bound.

## 8. Interfaces and out-of-scope

- **Consumes:** Qafiya spec v1.1 (datasets, bindings, spine discipline); the efficiency-experiment harness as measurement starting point; the defect-eval battery.
- **Exposes (for Layers 2–4, 6):** a `route(request) → decision + bound record` interface; refusal as a typed outcome. Layers above never pick models themselves.
- **Out of scope:** what the model says (Layers 2–4), the shell (Layer 6), learned-router research beyond an ablation.

## 9. Deliverables and stop points

`brief.md` (this file) → **owner confirmation** → `spec.md` (formal policy-as-data schema, decision procedure, decision-log binding format) → prototype → frozen experiments → paper draft (.tex).

**Confirmation record:** items (a)–(d) were confirmed by the owner on 2026-10-22 — see the
status block at the top of this file for the per-item wording and the two caveats that bind
the design (parameters must stay user-configurable data; this layer is exploratory, so frozen
results are allowed to change the next policy version).

**Next deliverable:** `spec.md` — policy-as-data schema, decision procedure, decision-log
binding format. Then prototype, then frozen experiments, then paper draft.
