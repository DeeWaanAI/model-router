# FREEZE — Experiment 2: Privacy Policy Compliance (pre-registration)

**Frozen:** 2026-10-22 · **Run:** `exp2` · **Cost:** **$0** — every decision is made with
`dry_run=True`, so no model is called (spec §5.2). This file was written **before** the run.

## 1. Frozen artifacts

| Artifact | Value |
|---|---|
| Battery | `battery/prompts.jsonl` |
| Battery sha256 | `bd833e8835a6b46c5325b5c087aafad653346ee4db177b73604edd1347e0268b` |
| Battery bytes / n | 7,798 bytes / **44 prompts**, 44 unique ids |
| Ground-truth labels | **18 private · 11 confidential · 15 public** |
| Adversarial subset | **9** (5 private-behind-public-camouflage, 4 public-carrying-sensitive-vocabulary) |
| Manifest | `battery/manifest.json` |

## 2. Pinned inputs (the run is reproducible from these four identifiers)

| Input | Value |
|---|---|
| Policy | `pol_179269431986400003` (label `v1`) |
| Privacy ruleset | `rul_179269431985500001` (8 rules, default `private`, fail closed) |
| Task ruleset | `rul_179269431986100002` (7 rules, default `open_chat`) |
| Registry snapshot | `cff3c0ffa2a04577dbfbef1998eced8c2422c6e5634df117d15ce44adbb1bc5d` |
| Active variants | 6 (2 local, 4 cloud) |
| `ds:capabilities` rows | **0** — eligibility coverage is 0%, so every gate-2 pass is `allow_flagged` |

## 3. Method

Each battery prompt becomes one request routed with `dry_run=True` under run label `exp2`.
Gates 1–3 run, the decision and its gate trace are written to `ds:decision_log`, and a
per-decision claim binding is written. No inference, no tokens, no spend. After the run,
one aggregate claim binding (spec §4.2) records the headline compliance number with both
sides, and `router/audit.py` replays every decision and runs the proof-of-absence scan.

## 4. Pre-registered metrics

| # | Metric | Definition |
|---|---|---|
| M1 | **Gate compliance** | decisions with `privacy_class = private` **as classified** whose chosen locus is cloud. Both sides recorded |
| M2 | **End-to-end privacy compliance** | prompts whose **ground-truth label** is private whose chosen locus is cloud. This is the number that matters: M1 can be perfect while M2 is not |
| M3 | **Classifier accuracy** | agreement with ground-truth labels, overall and as a 3×3 confusion matrix |
| M4 | **Over-classification rate** | label public/confidential → classified `private`. The measured cost of failing closed |
| M5 | **Under-classification rate** | label `private` → classified less sensitive. The dangerous direction |
| M6 | **Privacy-label coverage** | share of prompts classified by a matched rule rather than the fail-closed default |
| M7 | **Proof-of-absence scan** | two forms: (a) needles = prompts *classified* private — a schema guarantee, expected PASS; (b) needles = prompts *labelled* private — detects text stored because the classifier erred |
| M8 | **Refusal rate** | share refused, broken down by §2.6 reason code |

## 5. Hypotheses, with their falsifiers

- **H1** M1 = 0 violations. This is a *proof* about a set filter over frozen data, not an
  empirical finding; a non-zero result is a code defect and stops the run.
- **H2** M3 < 1.00, with errors concentrated in the adversarial subset. Specifically the
  public-labelled prompts that carry sensitive vocabulary (`u12` credit-card validation,
  `u13` hospital records schema, `u14` address template) are expected to over-classify to
  `private`. Falsifier: perfect accuracy would mean the battery is not probing anything.
- **H3** All five camouflage prompts (`p15`–`p18`, and `p11`) classify as `private` or
  `confidential`, never `public`, because severity rank outranks the public marker.
  **Falsifier: any one of them classified `public` means the gate is bypassable by
  camouflage, and that is the headline result, not a caveat.**
- **H4** `p11` (a personal disciplinary letter) classifies as `confidential`, under-protecting
  a private matter — a measured M5 error caused by company-vocabulary keywords.
- **H5** M2 = 0 violations even where M3 < 1.00, because the expected errors are
  over-protective (fail-closed default plus severity ordering). **Falsifier: any truly
  private prompt reaching a cloud locus is reported as a defect with its decision id, not
  filtered out.**
- **H6** M8 ≈ 0 on this battery, because every prompt is short enough for the 8,192-token
  local window. A non-zero rate would indicate a gate-3 or gate-1 problem, not a length
  problem, and would be investigated before any claim is made.

## 6. Honest limits, declared before the results exist

1. **Single annotator.** Labels are the layer author's judgment, frozen before the run. No
   second annotator, no inter-rater agreement. A disagreement about whether `u13`
   (hospital record *schemas*, no individual's data) is public is a legitimate one, and it
   moves M3/M4/M5 but cannot move M1.
2. **"Private" is a policy choice, not a fact about the world.** The battery measures
   agreement with *this* owner's labelling, not with any universal standard.
3. **A keyword classifier on 44 short prompts.** No long documents, no attachments except
   one CSV marker, no non-English text, no multi-turn context. Gate-3 refusals are
   therefore under-represented relative to real use, and the over-classification rate is a
   property of *this* ruleset, not of the architecture.
4. **M1 is a proof, not evidence about the world.** It shows the gate does what the policy
   says. It cannot show the policy is right, and it cannot catch a misclassification —
   which is exactly why M2 exists and why the two are reported separately (brief §7.1).
5. **Eligibility coverage is 0%** for this run. Gate 2 contributes nothing here; the
   privacy result does not depend on it, and no claim in this experiment may cite
   verifiability.

## 7. Success criteria

- **SC1** M1 = 0 (H1). Non-negotiable; anything else is a defect.
- **SC2** M2 = 0 (H5). The headline number for the paper. Any violation is reported with
  its decision id and the rule that fired.
- **SC3** M7(a) PASS and M7(b) reported either way — a leak found by (b) is a result.
- **SC4** M3, M4, M5, M6 reported as measured, with the confusion matrix, whatever they are.
- **SC5** Every number above recomputed from the frozen log by `experiments/spine.py`,
  exit 0. If the spine fails, the number does not go in the paper.

---

# Amendment — the v1 run found a leak, and the v2 run is pre-registered here

**Written 2026-10-22, after the v1 run and BEFORE the v2 run.** Nothing in §1–§7 above is
altered; this adds a second run under a changed policy.

## A.1 What the v1 run measured (run label `exp2`, policy `pol_179269431986400003`)

Raw evidence retained, not overwritten: `results/raw/decisions_exp2-v1.jsonl`,
`results/raw/run_meta_exp2-v1.json`, and the 44 decisions in `ds:decision_log` under
`run = "exp2"`.

- **M1 gate compliance: PASS, 0 violations.** No request the classifier called private was
  routed to a cloud locus. H1 holds.
- **M7a proof-of-absence (22 classified-private needles): PASS.** The schema rule works.
- **M7b proof-of-absence (18 labelled-private needles): FAIL — one leak, `p11`.**
  *"I received a disciplinary letter at work; help me write my response to it."* was
  classified **confidential**, not private, exactly as H4 predicted. Because v1's rule is
  `store_prompt_content: never_for_private`, a confidential-classified prompt's text **is**
  stored — so a prompt whose ground-truth label is private is sitting in
  `decision_log/data.jsonl` and `history.jsonl`.
- Locus compliance (M2) was still 0 violations: confidential maps to local-only, so the
  text never left the device. **The leak is into the log, not to a cloud provider.**

This is the defect the scan exists to find, and it is a design defect rather than a coding
mistake: keying the no-text rule to the *classifier's* verdict makes log privacy only as
good as the classifier. H5 survives on locus and fails on log content, which is a result
the brief's §7.1 honesty clause anticipated but did not foresee in this direction.

Two mitigating facts, stated so the finding is not over-read: the battery is synthetic, so
no real person's data was exposed; and the leak is detectable, permanent-but-visible, and
bounded to the log.

## A.2 The change under test — policy v2 (`pol_179276708715100001`)

Identical to v1 except `logging.store_prompt_content` and `store_response_content` are
both **`never`**: no prompt or response text is stored for any class. Identity is still
preserved by the HMAC, and gate 3 stays replayable because `est_tokens_in` is logged per
variant (§1.6). A third enum value, `never_for_private_or_confidential`, was added to the
schema for owners who want the middle setting. v1 remains active and auditable; nothing was
deleted or edited.

## A.3 Pre-registered expectations for the v2 run (label `exp2-v2`)

- **M1** 0 violations — unchanged; the gates are untouched by this policy edit.
- **M7a** PASS.
- **M7b** PASS, **and this pass is structural, not empirical.** With no text stored there
  is nothing for the scan to find, so the scan confirms the absence of the mechanism rather
  than the accuracy of the classifier. The paper MUST say so; reporting it as "leak fixed,
  scan passes" without that qualification would be an S5-class over-claim. The classifier
  still mislabels `p11` — v2 removes the *consequence* of the mislabel, not the error.
- **M3–M6 unchanged**, because the classifier and rulesets are identical. Any difference
  would indicate the policy edit changed classification, which it must not.
- **Declared cost of v2:** the audit can no longer re-derive token counts from stored text,
  so `gate3_token_estimate_unverifiable` rises from 22/44 to **44/44**. That is a real
  reduction in replay strength, traded for removing a leak path. It is reported as a
  coverage number (§6.3), not hidden.

## A.4 What would falsify the fix

Any v2 decision record containing prompt or response text; or M3–M6 differing from v1; or
an M1 violation appearing in v2 that was absent in v1. All three are checked by the spine.


---

# Results — recorded after the runs, 2026-10-22/23

**Cost of every run: $0.** All 176 decisions were made with `dry_run=True`; no model was
called. Spine exit code **0**: every number below recomputes from the frozen battery and the
frozen decision log, and the raw run file agrees with the log field by field.

## R.1 Four runs

| Run | Policy | Matcher | M1 gate viol. | M2 end-to-end viol. | M3 accuracy | M4 over | M5 under | M6 label cov. | text stored | M7 scoped | M7 standing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `exp2` | v1 (`never_for_private`) | substring | **0/22** | **0/18** | 0.8182 | 7 | 1 | 0.9545 | 22/44 | **LEAK p11** | LEAK p11 |
| `exp2-v2` | v2 (`never`) | substring | **0/22** | **0/18** | 0.8182 | 7 | 1 | 0.9545 | 0/44 | clean | LEAK p11 |
| `exp2-v2b` | v2 (`never`) | word-boundary | **0/23** | **0/18** | 0.8182 | 7 | 1 | 0.9318 | 0/44 | clean | LEAK p11 |
| `exp2-v3` | v3 arm (`confidential→cloud`) | word-boundary | **0/23** | **0/18** | 0.8182 | 7 | 1 | 0.9318 | 0/44 | clean | LEAK p11 |

M8 refusals: **0 in all four runs** — every battery prompt fits the local 8,192-token window.

Confusion matrix (identical in every run except the one cell noted):
`private → {private 17, confidential 1}` · `confidential → {confidential 11}` ·
`public → {public 8, private 5, confidential 2}` under substring matching, and
`{public 8, private 6, confidential 1}` under word-boundary matching. The single change is
`u10` — see R.3.

## R.2 Hypothesis verdicts

- **H1 confirmed.** M1 = 0 violations in all four runs. As pre-registered, this is a proof
  about a set filter over frozen data, not evidence about the world.
- **H2 confirmed.** M3 = 0.8182, and all four public-labelled prompts carrying sensitive
  vocabulary over-classified: `u12` (credit-card validation, rank 3), `u13` (hospital record
  schemas, rank 2), `u14` (address template, rank 4), `c12` (a now-public roadmap, rank 7).
- **H3 confirmed — the gate is NOT bypassable by camouflage on this battery.** All four
  camouflage prompts classified private: `p15` rank 2 (medical), `p16` rank 3 (credit card),
  `p17` rank 2 (diagnosis inside a translate task), `p18` rank 3 (a real SSN inside a python
  task — severity rank beat the rank-8 `python` public marker, which is exactly what the
  ordering is for).
- **H4 confirmed.** `p11` (a personal disciplinary letter) classified `confidential` at
  rank 6 — the only M5 under-classification.
- **H5 confirmed on locus, FAILED on log content under v1.** No truly-private prompt reached
  a cloud locus in any run. But under v1 the text of `p11` was written to the decision log,
  because the no-text rule was keyed to the classifier's verdict. That is the defect policy
  v2 removes, and it is the single most valuable result of this experiment.
- **H6 confirmed.** 0 refusals.

## R.3 Two defects found, and what each fix did

1. **Log leak via classifier-keyed logging (v1 → v2).** `p11` was classified confidential, so
   `store_prompt_content: never_for_private` permitted storing its text; the prompt is
   labelled private, so a private prompt entered `data.jsonl` and `history.jsonl`. Fixed by
   policy v2 (`never` for all classes). Verified: v2/v2b/v3 store text for 0/44 decisions,
   and their scoped absence scans are clean.
   **The fix does not retract the leak.** Under append-only provenance the v1 text is still
   in the log, so the *standing* scan fails permanently and correctly. Amendment A.3
   predicted M7b would PASS; that prediction was **wrong**, because it did not account for
   the log being cumulative. Recorded here rather than quietly dropped.
2. **Substring keyword matching (→ word boundaries).** The confidential keyword `nda` matched
   inside "pa**nda**s", so `u10` ("read a CSV file in pandas") was classified confidential at
   rank 7 instead of falling through to the fail-closed default. Fixed in the matcher.
   Measured effect, deliberately not over-claimed: it changes exactly **one** classification
   on this battery (`u10`: confidential → private), M3 is **unchanged** at 0.8182 because
   `u10` was over-classified either way, and M6 falls from 0.9545 to 0.9318 because `u10`
   now takes the default path. The fix removes a false-positive mechanism; it did not
   improve accuracy on this battery, and the spine asserts both facts.

## R.4 Mechanism check — the two-key property (`mechanism_long_confidential.py`, $0)

A long prompt (1,400 filler rows, ~10k tokens, exceeding the local window) written in
company vocabulary only, so it classifies `confidential` at rank 6 while its ground truth is
private (one named individual's own employment record):

| Configuration | Gate-1 exclusion reason | Outcome |
|---|---|---|
| v2 (`confidential → local`) | `locus_cloud_not_allowed_for_confidential` | **refused** (`context_exceeds_all`) |
| v3 arm (`confidential → local+cloud`) | `clearance_public_below_confidential` | **refused** — the map change alone did NOT expose it |
| v3 arm **plus** one cloud variant's `clearance` raised to `confidential` (throwaway store) | none | **routed to `qwen-flash`, locus = cloud** |

**Finding: gate 1 is a conjunction, and that is load-bearing.** The user-facing tunable the
owner flagged in caveat (a) — the privacy-class → locus map — is *not by itself* sufficient to
expose confidential text, because every cloud variant is registered with `clearance: public`
and gate 1 also requires `clearance ⊒ request_class`. Exposing data requires **two** changes:
the policy map *and* a registry-level clearance raise. Spec §2.1 claimed this fail-safe;
it is now demonstrated rather than asserted, and the demonstration ran in a throwaway store
so the real registry was never modified.

Boundary, stated plainly: this shows the mechanism and its two-key requirement. It does
**not** measure how often such a configuration arises in practice, and the battery-level M2
rate for long prompts is unmeasured — battery v2 with long items is queued.

## R.5 Coverage and audit state

Eligibility coverage is **0%** in every run (`ds:capabilities` is empty), so gate 2
contributed nothing and no claim here may cite verifiability. Binding coverage **100%**.
`gate3_token_estimate_unverifiable` rises from 22/44 (v1) to **44/44** (v2+) — the declared
price of storing no text: the audit can no longer re-derive token counts from the prompt and
must trust the logged `est_tokens_in`. The replay audit passes all §6.1 checks in every run;
its only failures are the §6.2 standing scans, which are the permanent v1 leak reported above.
