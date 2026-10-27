# Where Should This Query Run? Cost-, Privacy-, and Verifiability-Aware Model Routing: A Systematic Literature Review

**Scope:** the *routing decision* layer only — per-request selection among local and cloud models of different sizes and capabilities. What the chosen model says (view rendering, action, memory) is out of scope and owned by Layers 2–4.
**Review type:** structured thematic review (scoping + targeted synthesis).
**Date searched:** 2026-10-20. **Window:** 2018-01-01 to 2026-12-31.
**Databases:** OpenAlex (primary), arXiv (preprints). All included citations verified against the OpenAlex DOI resolver / arXiv API.

---

## Abstract

A personal AI system that owns both local models and cloud API access faces a question the serving-stack literature mostly ignores: *per request, which model answers, under which policy?* This review maps the five research literatures that bear on that question: (1) LLM routing and cascading foundations, (2) cost–quality frontier construction, (3) privacy-aware and local/cloud hybrid routing, (4) capability and query-difficulty estimation, and (5) verifiability, governance, and adversarial hazards of routed systems. From 1,671 records we screened 211 and synthesised 45 verified works. The central finding is that routing research optimises a **single soft currency** — cost subject to quality — and treats privacy, when it appears, as a penalty term in the same objective. No surveyed work defines routing as an **explicit policy over independent currencies** in which privacy is a hard gate (private-classified prompts provably never leave the device), **verifiability is a first-class eligibility constraint** on models (e.g. reasoning-mode models disqualified for prompt classes where they provably fail), and **every routing decision is itself a provenance-carrying, auditable record**. We state that gap as the layer's novelty claim and outline the evidence needed to defend it.

## 1. Introduction and research questions

The economics of LLM inference made "which model should answer?" a research topic almost as soon as commercial APIs existed: prices span orders of magnitude, and small models answer a large share of real queries correctly. The resulting literature is young (essentially 2023 onward), fast-moving, and scattered across ML venues, systems venues, and preprints. This review reorganises it around the design needs of a *personal* AI system, where the router answers not to a serving provider's throughput target but to a user's three currencies: money, privacy, and the ability to check what the system did.

**Primary RQ.** *What prior work exists toward per-request model routing that is simultaneously cost-aware, privacy-enforcing, and verifiability-aware — and where is the integration gap?*

Sub-questions:

- **RQ1 (foundations).** How do routing and cascading systems decide between models, and what signals do they consume?
- **RQ2 (cost–quality).** How is the cost–quality frontier constructed and exploited, and at what supervision cost?
- **RQ3 (privacy).** How has privacy entered routing decisions — as a constraint, a penalty, or an architecture?
- **RQ4 (capability estimation).** How do systems predict per-query which models can answer, and how well do difficulty estimates transfer?
- **RQ5 (hazards).** What is known about failure modes of routed systems themselves: unfaithful routing, adversarial manipulation, and models that are selectable but unusable for a task class?

## 2. Methods

### 2.1 Search strategy

Two reachable databases were queried programmatically (queries executed 2026-10-20): **OpenAlex** `title_and_abstract.search` and the **arXiv** API (`all:` phrase queries), both restricted to 2018–2026 and sorted by relevance. Six concept blocks were used; representative strings:

| Block | Example query strings |
|---|---|
| Routing & cascading | `"LLM routing"`, `"LLM cascade"`, `"FrugalGPT"`, `"RouteLLM"`, `routing between small and large language models` |
| Cost–quality | `"cost-efficient" "large language model" inference`, `"cost-optimal" LLM`, `adaptive inference LLM budget` |
| Privacy & local/cloud | `"on-device" "cloud" "large language model"`, `"edge-cloud" LLM offloading`, `local cloud language model privacy` |
| Capability & difficulty | `"query difficulty" LLM`, `model capability estimation LLM benchmark prediction` |
| Small–large delegation | `"small language model" "large language model" routing`, `"weak model" "strong model" LLM cascade` |
| Verifiability & structured output | `"structured output" "reasoning model" failure`, `language model tabular data reliability` |

A first arXiv pass with long phrases under-retrieved; a second pass with short quoted phrases was run and merged (both scripts and raw responses are preserved).

### 2.2 Screening and inclusion

Records were deduplicated by DOI (normalised-title fallback), then screened by a weighted keyword score over title+abstract (threshold ≥ 6) with explicit negative terms for off-topic uses of "routing" (networking, vehicle routing, VLSI), followed by manual curation to works that materially inform the routing decision. **Exclusion:** infrastructure-only serving optimisation (quantization, KV-cache, batching) retained only where it feeds a routing signal; domain-specific cascades (security scanners, games) excluded unless the routing method transfers.

### 2.3 PRISMA flow

| Stage | n |
|---|---|
| Identified — OpenAlex | 1,050 |
| Identified — arXiv (passes 1+2) | 621 |
| **Total identified** | **1,671** |
| After deduplication | 1,548 |
| Passed relevance screen | 211 |
| **Included in synthesis** | **45** |

### 2.4 Citation verification

Every included identifier was resolved against OpenAlex (DOI or work ID) or the arXiv API and canonical metadata (title, year, venue, authors) retrieved programmatically; two records lacking DOIs in OpenAlex were verified by OpenAlex work ID and their arXiv DOIs recovered from location metadata. Author lists are registry-derived and should be spot-checked at final typesetting.

## 3. Results — thematic synthesis

### 3.1 Routing and cascading foundations (RQ1)

The field opened with **FrugalGPT** [1], which framed LLM use as a sequential cascade — try cheap models first, escalate when a learned scorer doubts the answer — and demonstrated cost reductions of up to 98% at matched quality. **Mixture-of-Thoughts cascades** [2] extended the same idea to reasoning prompts. **RouteLLM** [3] trained routers on preference data to predict which of two models wins a comparison, and **Hybrid LLM** [4] cast the small-vs-large choice as a quality-aware binary decision. Benchmarks followed quickly — **RouterBench** [5], **RouterEval** [15], **LLMRouterBench** [44] — as did method consolidation: **AutoMix** [6] (self-verification-driven mixing), **A Unified Approach to Routing and Cascading** [8] (showing routing and cascading are points on one design space), budget-constrained cascade policy learning [9], **agreement-based cascading** [10] (escalate unless cheap models agree), abstention-driven savings [11], and probabilistic tuning of cascade thresholds [12]. Recent empirical anatomy — **Most of the LLM Routing Gap Is Task Type** [17] — finds that coarse task identity explains most of what routers learn, a result that both simplifies router design and warns against over-engineering.

*Synthesis.* The core machinery — a scorer, a threshold, an escalation path — is mature and repeatedly validated. But every foundational system defines the router's objective as **expected quality minus expected cost**: a single soft trade-off over one model zoo, assumed homogeneous in every other respect. No foundational work treats models as differing in *kind* (privacy class, output discipline) rather than only in price and accuracy.

### 3.2 Constructing the cost–quality frontier (RQ2)

A second literature refines the economics. **Adaptive LLM Routing under Budget Constraints** [13] and contextual cascades [9] optimise under explicit token budgets; **C3PO** [19] adds probabilistic cost constraints for reasoning cascades; **UCCI** [20] and **confidence-based small-to-large routing** [36] use calibrated uncertainty as the escalation signal; **SeqRoute** [22] and **cost-optimal routing with limited feedback** [24] attack the sequential/global budget problem; **Is Escalation Worth It?** [23] asks when deeper cascades pay at all. Supervision cost is addressed directly: **Routing Should Pay for Itself** [25] shows routers trained on sparse labels, and **budget-aware calibrated cascades** [21] bring the method to a production e-commerce setting.

*Synthesis.* The frontier is well understood, and the crossover logic — cheap model up to a difficulty threshold, expensive model beyond it — is exactly what our planned cost-quality experiment must reproduce as a baseline. Two observations carry over: uncertainty calibration is the dominant escalation signal, and supervision can be cheap. What none of these works provides is a frontier with **more than one constraint axis**: cost appears with quality, never jointly with privacy class or verifiability requirements.

### 3.3 Privacy-aware and local/cloud routing (RQ3)

Privacy has entered routing from three directions. **Architectural:** hybrid local–cloud designs that keep sensitive computation on-device — privacy-preserved cascades via CoT-enhanced policy learning [26], hybrid local-cloud architectures for privacy preservation [28], and the edge-SLM/cloud-LLM collaborative inference paradigm surveyed in [30] and [32]. **Benchmark:** **LLMSwitchBench** [31] measures edge–cloud routing for smart-home inference, the closest existing evaluation to a personal-AI setting. **Adversarial/auditing:** **Privacy-Preserving LLMs Routing** [27] protects the routing decision itself; **OCELOT** [29] introduces inference-leakage budgets for agents; and **Sensitive-Topic Leakage Through LLM Routing Metadata** [33] shows the *metadata* of routing decisions can itself leak — a caution any logged router must design against.

*Synthesis.* Privacy is real in this literature but is treated as (a) an architecture-level heuristic — "sensitive stuff stays local", without a formal gate or a compliance measurement — or (b) a soft penalty inside the same quality–cost objective. **No surveyed work makes privacy a hard, audited constraint** (100% of private-classified prompts provably local, measured Layer-1 style), and none treats the routing log as a privacy surface to be designed. The personal-AI context — one user, own device, own data — appears only in smart-home form [31].

### 3.4 Capability and query-difficulty estimation (RQ4)

Routers live or die on predicting which model can handle a query. Benchmark-data routing [7] showed model performance is learnable from public benchmarks; **ICL-Router** [18] learns model representations in-context; **SeLMRoute** [37] routes on probabilistic semantic evidence; **CSDiff** [34] routes from a difficulty lexicon with Bloom's-taxonomy multipliers; confidence-calibrated small–large collaboration [45] calibrates the handoff. Two cautionary results matter for design: **Register Bias** [35] shows complexity-based routers systematically misjudge queries by *linguistic register*, not task hardness; and the task-type finding [17] implies difficulty features mostly proxy task identity.

*Synthesis.* Capability estimation is accurate enough to route, but it estimates **average task performance on benchmarks**. It does not estimate *eligibility*: whether a model can be used at all for a given prompt class under a given discipline (structured output, termination, evidence binding). That is a different signal — closer to a compatibility matrix than to a difficulty score — and no surveyed work learns or maintains it.

### 3.5 Verifiability, governance, and adversarial hazards (RQ5)

A thin but growing literature examines the routed system as an object of trust. **VeriRouter** [38] makes routing decisions verifiable; **deterministic and auditable routing** [41] argues for rule-based routers in regulated environments. On the output side, **structured-output reliability in small language models** [39] shows small models can be correct yet unusable — schema violations, non-termination — and dual-dimensional structured-output testing [40] quantifies it; these are exactly the disqualification signals a verifiability-aware router must consume. On the attack side, the **Route to Rome** attack [42] steers routers toward expensive models with adversarial suffixes, and **cascade failure under adversarial attack** [43] shows efficiency-motivated escalation amplifies adversarial success.

*Synthesis.* This literature establishes that routing has its own attack surface and that output-discipline failures are measurable — but the two threads never meet: verifiability appears as a property of the *router's decision*, not as a **routing constraint derived from measured model behaviour** (e.g. "reasoning-mode models never terminate on tabular prompts, therefore disqualified for that class"). Treating a measured model hazard as a routing input is, to our knowledge, absent.

## 4. The gap and the layer's novelty claim

The five literatures each solve one currency and leave the seams open:

- Routing/cascading optimises cost subject to quality — one soft objective, one homogeneous model zoo [1–12].
- Frontier methods refine that objective but add no new constraint axes [13–25].
- Privacy work makes locality architectural or penalised, never a hard, audited gate [26–33].
- Capability estimation predicts benchmark difficulty, not eligibility under an output discipline [7, 17, 18, 34, 35, 37, 45].
- Trust work verifies router decisions or measures output hazards separately, never feeding measured hazards back as routing constraints [38–43].

**Novelty claim (defensible).** *We propose model routing as an explicit policy over three independent currencies — cost, privacy, and verifiability — in which (i) privacy is a hard gate with measured 100% compliance for private-classified prompts, (ii) verifiability is an eligibility constraint derived from frozen per-model measurements (models that provably fail a prompt class are excluded before cost is considered), (iii) the policy itself is data — a provenance-carrying dataset of models × capabilities × prices × privacy class under the Qafiya spec — and (iv) every routing decision is logged as a bound, auditable record. The contribution is the policy algebra and its enforcement, not another accuracy-per-dollar router.*

**Closest neighbours to differentiate explicitly:** [1] (cost cascade — no privacy, no verifiability), [26]/[31] (privacy-aware routing — soft heuristic, no audited hard gate), [38]/[41] (verifiable routing — verifies the decision, not model eligibility), [39] (measures the structured-output hazard — but does not route on it).

## 5. Evidence required to defend the claim (next steps)

A routing-layer paper is credible with: (a) the **policy-as-data spec** (models × capabilities × prices × privacy class, Qafiya-bound); (b) a **prototype** routing between Together API and a local backend with every decision logged as a bound record; (c) **frozen experiments** — cost-quality frontier with crossover points vs always-big/always-small baselines; 100% local retention of private-classified prompts; and the verifiability interaction measured on the Qafiya defect battery, quantifying the reasoning-model hazard as a routing constraint. The router's decision procedure should be positioned as *deterministic on gates (privacy, eligibility)* and *learned/heuristic only within the feasible set*, not as a learned arbiter of everything.

## 6. Limitations of this review

Single-pass automated search on two databases (Semantic Scholar excluded); relevance screening used a keyword score plus manual curation rather than dual independent review, so recall is bounded by the query strings. The 2023–2026 concentration of the field means many included items are recent preprints not yet peer-reviewed; venues are reported as returned by the registries. Author metadata is registry-derived and should be re-checked at typesetting. These bound the review's completeness, not the direction of the gap, which is corroborated independently across all five themes.

## 7. References

1. Chen, L., Zaharia, M., Zou, J. (2023). *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.* arXiv. https://doi.org/10.48550/arXiv.2305.05176
2. Yue, M., Zhao, J., Zhang, M., et al. (2023). *Large Language Model Cascades with Mixture of Thoughts Representations for Cost-efficient Reasoning.* arXiv. https://doi.org/10.48550/arXiv.2310.03094
3. Ong, I., Almahairi, A., Wu, V., et al. (2024). *RouteLLM: Learning to Route LLMs with Preference Data.* arXiv. https://doi.org/10.48550/arXiv.2406.18665
4. Ding, D., Mallick, A., Wang, C., et al. (2024). *Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing.* arXiv. https://doi.org/10.48550/arXiv.2404.14618
5. Hu, Q. J., Bieker, J., Li, X., et al. (2024). *RouterBench: A Benchmark for Multi-LLM Routing System.* arXiv. https://doi.org/10.48550/arXiv.2403.12031
6. Aggarwal, P., Madaan, A., Anand, A., et al. (2024). *AutoMix: Automatically Mixing Language Models.* NeurIPS. https://doi.org/10.52202/079017-4164
7. Shnitzer, T., Ou, A., Silva, M., et al. (2023). *Large Language Model Routing with Benchmark Datasets.* arXiv. https://doi.org/10.48550/arXiv.2309.15789
8. Dekoninck, J., Baader, M., Vechev, M. (2024). *A Unified Approach to Routing and Cascading for LLMs.* arXiv. https://doi.org/10.48550/arXiv.2410.10347
9. Zhang, X., Huang, Z., Taga, E. O., et al. (2024). *Efficient Contextual LLM Cascades through Budget-Constrained Policy Learning.* NeurIPS. https://doi.org/10.52202/079017-2910
10. Kolawole, S., Dennis, D., Talwalkar, A. (2024). *Agreement-Based Cascading for Efficient Inference.* arXiv. https://doi.org/10.48550/arXiv.2407.02348
11. Zellinger, M. J., Liu, R., Thomson, M. (2025). *Cost-Saving LLM Cascades with Early Abstention.* arXiv. https://doi.org/10.48550/arXiv.2502.09054
12. Zellinger, M. J., Thomson, M. (2025). *Rational Tuning of LLM Cascades via Probabilistic Modeling.* arXiv. https://doi.org/10.48550/arXiv.2501.09345
13. Panda, P., Magazine, R., Devaguptapu, C. (2025). *Adaptive LLM Routing under Budget Constraints.* EMNLP Findings. https://doi.org/10.18653/v1/2025.findings-emnlp.1301
14. Srivatsa, K. A., Maurya, K. K., Kochmar, E. (2024). *Harnessing the Power of Multiple Minds: Lessons Learned from LLM Routing.* arXiv. https://doi.org/10.48550/arXiv.2405.00467
15. Huang, Z., Ling, G., Lin, Y., et al. (2025). *RouterEval: A Comprehensive Benchmark for Routing LLMs to Explore Model-level Scaling Up in LLMs.* EMNLP Findings. https://doi.org/10.18653/v1/2025.findings-emnlp.208
16. Tran, C., Paracha, S., Hafeez, A. (2025). *Arch-Router: Aligning LLM Routing with Human Preferences.* arXiv. https://doi.org/10.48550/arXiv.2506.16655
17. Lee, J. (2026). *Most of the LLM Routing Gap Is Task Type.* arXiv. https://doi.org/10.48550/arXiv.2608.23023
18. Wang, C., Li, H., Zhang, Y., et al. (2025). *ICL-Router: In-Context Learned Model Representations for LLM Routing.* arXiv. https://doi.org/10.48550/arXiv.2510.09719
19. Valkanas, A., Pal, S., Rumiantsev, P., et al. (2025). *C3PO: Optimized Large Language Model Cascades with Probabilistic Cost Constraints for Reasoning.* arXiv. https://doi.org/10.48550/arXiv.2511.07396
20. Kotte, V. (2026). *UCCI: Calibrated Uncertainty for Cost-Optimal LLM Cascade Routing.* arXiv. https://doi.org/10.48550/arXiv.2605.18796
21. Divi, V. R. (2025). *Budget-Aware Calibrated Cascades for E-Commerce Customer Assistants: A Cost–Quality Routing Benchmark.* Zenodo. https://doi.org/10.5281/zenodo.21869763
22. Xu, Z., Zheng, S., Wang, W. (2026). *SeqRoute: Global Budget-Aware Sequential LLM Routing via Offline Reinforcement Learning.* arXiv. https://doi.org/10.48550/arXiv.2605.25424
23. Bouchard, D. (2026). *Is Escalation Worth It? On the Depth of LLM Cascades.* arXiv. https://doi.org/10.48550/arXiv.2605.06350
24. Woisetschläger, H., Mammadli, A., Zhang, R., et al. (2026). *Cost-Optimal LLM Routing with Limited User Feedback under User Satisfaction Guarantees.* arXiv. https://doi.org/10.48550/arXiv.2606.19376
25. Lai, G., Bian, G., Ma, H.-X., et al. (2026). *Routing Should Pay for Itself: Sparse Supervision for Economical LLM Routing.* arXiv. https://doi.org/10.48550/arXiv.2609.37402
26. Zhang, K., Wang, C., Peng, L. (2024). *Privacy-preserved LLM Cascade via CoT-enhanced Policy Learning.* arXiv. https://doi.org/10.48550/arXiv.2410.08014
27. Wu, X., Zhang, Y., Ji, Y., et al. (2026). *Privacy-Preserving LLMs Routing.* arXiv. https://doi.org/10.48550/arXiv.2604.15728
28. Bindra, A. S., Kaur, B., Mittal, M., et al. (2026). *A Hybrid Local-Cloud Large Language Model Architecture for Privacy Preservation and Cost-efficient Inference.* INDIACom. https://doi.org/10.23919/indiacom70271.2026.11525699
29. Xie, J., Li, S. (2026). *OCELOT: Inference-Leakage Budgets for Privacy-Preserving LLM Agents.* arXiv. https://doi.org/10.48550/arXiv.2606.12341
30. Li, S., Wang, H., Xu, W., et al. (2026). *Collaborative Inference and Learning between Edge SLMs and Cloud LLMs: A Survey of Algorithms, Execution, and Open Challenges.* ACM Computing Surveys. https://doi.org/10.1145/3838593
31. Liu, Z., Guo, P., Wang, P. (2025). *LLMSwitchBench: A New Edge-Cloud Routing Benchmark for Smart Home LLM Inference.* IEEE Access. https://doi.org/10.1109/access.2025.3632276
32. Cai, G., Tian, R., Yang, L., et al. (2025). *Efficient Inference for Edge Large Language Models: A Survey.* Tsinghua Science & Technology. https://doi.org/10.26599/tst.2025.9010166
33. Chen, T.-R. (2026). *Sensitive-Topic Leakage Through LLM Routing Metadata: Measurement and Mitigation.* arXiv. https://doi.org/10.48550/arXiv.2610.09981
34. Olenick, B. (2026). *CSDiff: Training-Free LLM Task Routing via a CS Difficulty Lexicon and Bloom's Taxonomy Verb Multipliers.* Zenodo. https://doi.org/10.5281/zenodo.20388407
35. Koul, S. (2026). *Register Bias in Complexity-Based Large Language Model Routing.* arXiv. https://doi.org/10.48550/arXiv.2609.17542
36. Hossen, M. S., Rahman, A., Shaha, P., et al. (2026). *Who Pays for Efficient Inference? Confidence-Based Small-to-Large LLM Routing Across Tasks and Languages.* arXiv. https://doi.org/10.48550/arXiv.2610.23249
37. Perifanis, V., Pavlidis, N., Symeonidis, S. (2026). *SeLMRoute: Probabilistic Semantic Evidence for Large Language Model Routing.* arXiv. https://doi.org/10.48550/arXiv.2609.34736
38. Yao, M., Zhang, Z., Li, S., et al. (2026). *VeriRouter: Towards Verifiable LLM Routing.* arXiv. https://doi.org/10.48550/arXiv.2610.17431
39. Galeone, C., Park, M., Ettorre, G. (2026). *When Correct Isn't Usable: Improving Structured Output Reliability in Small Language Models.* arXiv. https://doi.org/10.48550/arXiv.2605.02363
40. Zhang, C. (2026). *Research on Structured Output of Large Language Models Based on Dual-Dimensional Testing.* Applied and Computational Engineering. https://doi.org/10.54254/2755-2721/2026.gl37071
41. Barcelos, F. (2026). *Deterministic and Auditable Routing for Artificial Intelligence in Regulated Environments.* Zenodo. https://doi.org/10.5281/zenodo.20531686
42. Tang, H., Yan, Y., Lu, J., et al. (2026). *Route to Rome Attack: Directing LLM Routers to Expensive Models via Adversarial Suffix Optimization.* ACL. https://doi.org/10.18653/v1/2026.acl-long.2051
43. Sun, Z., Chen, D., Li, S. (2026). *When Efficiency Backfires: Cascading LLMs Trigger Cascade Failure under Adversarial Attack.* arXiv. https://doi.org/10.48550/arXiv.2605.17288
44. Li, H., Zhang, Y., Guo, Z., et al. (2026). *LLMRouterBench: A Massive Benchmark and Unified Framework for LLM Routing.* arXiv. https://doi.org/10.48550/arXiv.2601.07206
45. Zhang, C., Zhu, Z., Wei, Y., et al. (2026). *Confidence-Calibrated Small-Large Language Model Collaboration for Cost-Efficient Reasoning.* arXiv. https://doi.org/10.48550/arXiv.2603.03752

---
*Reproducibility artifacts:* `search.py` (OpenAlex + arXiv pass 1), `search_arxiv_pass2.py` (short-phrase arXiv pass), `screen.py` (dedupe + relevance screen), `verify.py` (citation verification), `raw/openalex.json`, `raw/arxiv.json`, `raw/combined.json`, `raw/screened.json`, `raw/included_verified.json` (canonical metadata for all 45). Generated 2026-10-20.
