# A Provenance-First Data Model for LLM-Mediated Personal Computing: A Systematic Literature Review

**Scope:** the *data model* layer only (typed records/blocks, provenance, claim-to-evidence binding, data↔view binding). The operating-system layer is explicitly out of scope and reserved for a later paper.
**Review type:** structured thematic review (scoping + targeted synthesis).
**Date searched:** 2026-10-07 (Asia/Bangkok). **Window:** 2018-01-01 to 2026-12-31.
**Databases:** OpenAlex (primary), arXiv (preprints). All included citations verified against the OpenAlex DOI resolver / arXiv API.

---

## Abstract

The prospect of a personal computing environment in which an LLM answers natural-language requests by generating *views* over the user's own data depends, more than on any model, on the **data substrate** beneath it. This review maps the four research literatures that a "provenance-first data model" must stand on: (1) semantic and generative file systems, (2) typed/structured data models and declarative view grammars, (3) data provenance and lineage, and (4) claim-to-evidence faithfulness and table verification — with a fifth adjacent literature on generative/just-in-time user interfaces that supplies the data↔view binding problem. From 644 records we screened 265 and synthesised 38 verified works. The central finding is that these communities have converged on *pieces* of the target system but not on a *unified substrate*: file-system work exposes data to language models but treats it as opaque text; generative-UI work renders views but discards provenance; verification work checks claims only as a post-hoc audit of finished text. No surveyed work defines a data model in which typed records/blocks carry **provenance and claim-to-evidence bindings as first-class fields**, so that every generated view is simultaneously interactive, editable back to source, and auditable. We state that gap as the paper's novelty claim and outline the evidence needed to defend it.

## 1. Introduction and research questions

A recurring vision — "just prompt the system and see results" — requires that personal data be represented in a form a language model can read, query, render, and safely write back. The design space for such a substrate is not new; it is distributed across research communities that rarely cite one another. This review reorganises that literature around a single question.

**Primary RQ.** *What prior work exists toward a data model that lets an LLM mediate access to a user's own data through generated, interactive, and verifiable views — and where is the integration gap?*

Sub-questions:

- **RQ1 (access).** How have semantic/generative file systems exposed personal data to language models, and what do they assume about the data's structure?
- **RQ2 (structure).** What typed or declarative representations (schemas, grammars, block models) exist for turning data into views?
- **RQ3 (provenance).** How is provenance/lineage represented, and has it ever been bound to *derived artifacts* (a rendered view, a summary claim) rather than to workflows alone?
- **RQ4 (faithfulness).** What methods verify that a statement is faithful to underlying tabular/structured data, and are they used *at generation time* or only as post-hoc audit?
- **RQ5 (binding).** How do generative-UI systems bind a generated view to its data, and is provenance carried through that binding?

## 2. Methods

### 2.1 Search strategy

Two reachable databases were queried programmatically (queries executed 2026-10-07): **OpenAlex** `title_and_abstract.search` and **arXiv** `abs:`/`ti:` field queries, both restricted to 2018–2026 and sorted by relevance/citations. Semantic Scholar was rate-limited (HTTP 429) and excluded. Five concept blocks were used; representative strings:

| Block | Example query strings |
|---|---|
| Semantic/generative file systems | `"semantic file system"`, `"natural language" file system`, `"large language model" "file system"` |
| Typed/structured data & views | `"block-based" document`, `typed records schema data model`, `declarative visualization grammar` |
| Provenance & lineage | `data provenance`, `lineage data query`, `"provenance" "language model"` |
| Claim faithfulness & table verification | `table fact verification`, `faithfulness summarization LLM`, `attribution LLM citation` |
| Generative/JIT UI | `"generative user interface"`, `"dynamic application" interface generation`, `model-generated interface` |

### 2.2 Screening and inclusion

Records were deduplicated by DOI (title fallback), then screened by a keyword-relevance score over title+abstract against the data-model topic (threshold ≥ 4), followed by manual curation to works that materially inform the substrate. **Exclusion:** domain-specific provenance (IoT/blockchain/medical/bioinformatics pipelines) retained only where the *data-model* idea transfers; pure summarisation-quality papers without a data-binding angle excluded.

### 2.3 PRISMA flow

![PRISMA flow](figures/prisma.svg)

| Stage | n |
|---|---|
| Identified — OpenAlex | 548 |
| Identified — arXiv | 96 |
| **Total identified** | **644** |
| After deduplication | 546 |
| Passed relevance screen | 265 |
| **Included in synthesis** | **38** |

### 2.4 Citation verification

Every included identifier was resolved against OpenAlex (DOI) or the arXiv API and canonical metadata (title, year, venue, authors) retrieved programmatically; three initial title-fallback matches that resolved to unrelated records were corrected or dropped. Author lists are reported as returned by OpenAlex and should be spot-checked at final typesetting.

## 3. Results — thematic synthesis

### 3.1 Semantic and generative file systems (RQ1)

Pre-LLM semantic file systems replaced path navigation with attribute/ontology queries — non-hierarchical directory navigation [4] and event-driven semantic stores [5] established the pattern of *describing* files rather than *locating* them. The LLM era reframed this as a query interface: **From Commands to Prompts** [1] puts a natural-language semantic file system *inside* an LLM agent OS (AIOS), letting the agent retrieve and act on files through prompts — the closest existing neighbour to the target system, and the work this paper must differentiate against. Most recently, the file system itself becomes *generated*: **Generative File Systems** [2] and **SYSSPEC** [3] synthesise and evolve file-system behaviour from natural-language specifications via LLMs. Framing work [6,7] generalises "LLM as OS, agents as apps."

*Synthesis.* Across this block, data is exposed to the model but treated as **opaque text or as file metadata**; none imposes a typed record structure, and none carries a binding from a rendered result back to the record that produced it. The interface problem is solved; the *substrate* problem is not.

### 3.2 Typed/structured data models and declarative view grammars (RQ2)

Two threads matter. The **data-shape** thread: formal schemas and type systems for semi-structured/JSON data [8], schema inference over arbitrary datasets [13], and schema-agnostic matching with pretrained models [14] give rigorous ways to type records without a fixed relational schema — directly relevant to a JSONL+schema substrate. The **view-shape** thread: declarative grammars — Vega/Altair [9], responsive-visualisation grammar Cicero [10], the grammar of tree visualisations GoTree [11], and grammar-based procedural generation Atlas [12] — prove that a *small composable spec* can render rich, interactive views, including the folder/sub-folder trees the target system needs. LLM-driven authoring closes the loop from intent to spec: **Data Formulator** [15] generates concept-driven visualisation specs from natural language, and **LIDA** [36] auto-generates grammar-agnostic visualisations.

*Synthesis.* The ingredients of "model emits a declarative spec; a deterministic renderer draws it" are fully mature and independently validated. What is missing is a **data model that is simultaneously the typed source and the provenance carrier** for those specs — grammars render data but assume it is already correct and already located.

### 3.3 Data provenance and lineage (RQ3)

Provenance is a mature discipline: workflow/data-provenance infrastructure such as **AiiDA** [16], surveys of provenance-aware knowledge representation and contextualised knowledge graphs [18], and provenance for security/privacy [21] and for responsible-AI governance [19]. A distinct and highly relevant line binds provenance to *generated text*: **Auditing Data Provenance in Text-Generation Models** [17] traces which training data a model output drew on, and **HaLLMark** [20] surfaces provenance/transparent-use of LLM edits to *writing* through interactive visualisation.

*Synthesis.* Provenance has been attached to (a) computational workflows and (b) model *training* data, and surfaced in writing tools — but **not embedded as a first-class field of a personal-data record that a generated view inherits**. The mechanism exists; the application to a live, user-owned, view-producing data store does not appear.

### 3.4 Claim-to-evidence faithfulness and table verification (RQ4)

The strongest formal precedent for "does this sentence mean the data" is **table-based fact verification**: the TabFact benchmark [22], structure-aware transformers [23], semantic-program generation for tabular claims [24], and open-domain systems [25], with evidence-revisiting methods [26] emphasising *which cells* support a claim. This has migrated to LLM faithfulness: **TabFaith** [27] benchmarks *structural* faithfulness of LLM table summaries; self-improving attribution [28] and critique-refinement for faithful summarisation [29] improve it; and the citation-generation and atomic-factuality families — **ALCE** [30], **FActScore** [31] — score whether generated claims are supported and attributable.

*Synthesis.* Verification is powerful but overwhelmingly **post-hoc and external**: it audits a finished draft or scores a model offline. The target system needs the same relation *inverted into a generation discipline* — the model emits a claim **bound to its evidence rows at write time**, so the check is cheap and the binding doubles as the edit/write-back target. No surveyed work makes that binding a property of the data structure itself.

### 3.5 Generative / just-in-time UI and the data↔view binding (RQ5)

Generative UI is now an active subfield: a working definition of designing generative UIs [33], LLM-generated interactive interfaces [32], personalisation of generative UIs [34], and "UI-DSL bricks" balancing transparency and controllability [35] — plus the adjacent "software as content / dynamic applications" framing [38] and conversational code assistants [37]. These systems generate *interactive* views that behave like the target (edit a cell without re-prompting).

*Synthesis.* They bind views to data for *interaction*, but the binding is **ephemeral and unverified**: the generated component is not tied to a durable, provenance-carrying record, so edits and claims are not auditable to a source. The missing piece is not the UI generation — it is the **stable, provenance-bearing substrate the UI is generated *from* and writes *back to***.

## 4. The gap and the paper's novelty claim

![Gap map](figures/gapmap.svg)

The five literatures each solve one facet and leave the seam between them open:

- File systems expose data but assume it is untyped text [1,2,3,4,5].
- View grammars render typed data but assume it is already correct and located [9–12,15].
- Provenance binds workflows and training data, not user-owned records feeding generated views [16–21].
- Verification audits finished text post-hoc, rather than binding claims to evidence at generation time [22–31].
- Generative UI binds views to data for interaction but discards provenance and durability [32–38].

**Novelty claim (defensible).** *We propose a provenance-first data model in which typed records/blocks carry, as first-class fields, (i) a schema/type, (ii) evidence provenance to their source, and (iii) claim-to-evidence bindings — so that a single substrate supports natural-language querying, deterministic just-in-time view rendering, durable write-back, and built-in auditability of every generated claim.* The contribution is the **integration and the elevation of provenance into the data model**, not any one component.

**Closest neighbours to differentiate explicitly:** [1] (semantic file system in an LLM OS — no typed/provenance substrate), [15] (LLM→visualisation spec — no provenance or write-back), [27]/[30] (faithfulness/attribution — post-hoc, not a data-model property), [35]/[38] (generative UI — no durable provenance binding).

## 5. Evidence required to defend the claim (next steps)

A data-model paper is credible with: (a) a **formal spec** of the record/block schema with provenance and claim fields; (b) a **prototype** demonstrating query→typed-record→generated interactive view→edit→write-back→provenance-traceable; (c) an **evaluation** — either a defect-catch benchmark (inject direction/scope/causal errors into generated summaries and show the claim-binding catches them, reusing the claim-to-evidence methodology already validated in the authors' prior work), or a small user study on edit-durability and trust. The verification layer should be positioned as *deterministic for provenance (proof)* and *advisory for semantics (flag)*, not as a certainty oracle.

## 6. Limitations of this review

Single-pass automated search on two databases (Semantic Scholar unavailable); relevance screening used a keyword score plus manual curation rather than dual independent review, so recall is bounded by the query strings. Pre-2018 foundational work (classic semantic file systems, compound documents, end-user programming) is acknowledged but not exhaustively covered given the 2018–2026 window. Several 2026 items are recent preprints not yet peer-reviewed. Author metadata is registry-derived and should be re-checked at typesetting. These bound the review's completeness, not the direction of the gap, which is corroborated independently across all five themes.

## 7. References

1. Shi, Z., Mei, K., Jin, M., et al. (2024). *From Commands to Prompts: LLM-based Semantic File System for AIOS.* arXiv. https://doi.org/10.48550/arXiv.2410.11843
2. Liu, Q., Zhang, H., Zou, M., et al. (2026). *Generative File Systems: Specification-Driven Synthesis and Evolution via LLMs.* ACM Transactions on Storage. https://doi.org/10.1145/3844149
3. Liu, Q., Zou, M., Zhang, H., et al. (2025). *Sharpen the Spec, Cut the Code: A Case for Generative File System with SYSSPEC.* arXiv. https://doi.org/10.48550/arXiv.2512.13047
4. Mashwani, S. R., Khusro, S. (2019). *360° Semantic File System: Augmented Directory Navigation for Nonhierarchical Retrieval of Files.* IEEE Access. https://doi.org/10.1109/access.2018.2890165
5. Vef, M.-A., Steiner, R., Salkhordeh, R., et al. (2020). *DelveFS — An Event-Driven Semantic File System for Object Stores.* IEEE Cluster. https://doi.org/10.1109/cluster49012.2020.00014
6. Mei, K., Zhu, X., Xu, W., et al. (2024). *AIOS: LLM Agent Operating System.* arXiv. https://doi.org/10.48550/arXiv.2403.16971
7. Ge, Y., Ren, Y., Hua, W., et al. (2023). *LLM as OS, Agents as Apps: Envisioning AIOS, Agents and the AIOS-Agent Ecosystem.* arXiv. https://doi.org/10.48550/arXiv.2312.03815
8. Baazizi, M.-A., Colazzo, D., Ghelli, G., et al. (2019). *Schemas and Types for JSON Data.* ACM SIGMOD. https://doi.org/10.1145/3299869.3314032
9. VanderPlas, J., Granger, B., Heer, J., et al. (2018). *Altair: Interactive Statistical Visualizations for Python.* JOSS. https://doi.org/10.21105/joss.01057
10. Kim, H., Rossi, R. A., Du, F., et al. (2022). *Cicero: A Declarative Grammar for Responsive Visualization.* ACM CHI. https://doi.org/10.1145/3491102.3517455
11. Li, G., Tian, M., Xu, Q., et al. (2020). *GoTree: A Grammar of Tree Visualizations.* ACM CHI. https://doi.org/10.1145/3313831.3376297
12. Liu, Z., Chen, C., Morales, F., et al. (2021). *Atlas: Grammar-based Procedural Generation of Data Visualizations.* IEEE VIS. https://doi.org/10.1109/vis49827.2021.9623315
13. Barret, N., Enache, T., Manolescu, I., et al. (2024). *Finding the PG schema of any (semi)structured dataset.* IEEE ICDE Workshops. https://doi.org/10.1109/icdew61823.2024.00055
14. Teong, K.-S., Soon, L.-K., Su, T. T. (2020). *Schema-Agnostic Entity Matching using Pre-trained Language Models.* ACM CIKM. https://doi.org/10.1145/3340531.3412131
15. Wang, C., Thompson, J., Lee, B. (2023). *Data Formulator: AI-powered Concept-driven Visualization Authoring.* IEEE TVCG. https://doi.org/10.1109/tvcg.2023.3326585
16. Huber, S. P., Zoupanos, S., Uhrin, M., et al. (2020). *AiiDA 1.0, a scalable computational infrastructure for automated reproducible workflows and data provenance.* Scientific Data. https://doi.org/10.1038/s41597-020-00638-4
17. Song, C., Shmatikov, V. (2019). *Auditing Data Provenance in Text-Generation Models.* ACM KDD. https://doi.org/10.1145/3292500.3330885
18. Sikos, L. F., Philp, D. (2020). *Provenance-Aware Knowledge Representation: A Survey of Data Models and Contextualized Knowledge Graphs.* Data Science and Engineering. https://doi.org/10.1007/s41019-020-00118-0
19. Werder, K., Ramesh, B., Zhang, R. (2022). *Establishing Data Provenance for Responsible Artificial Intelligence Systems.* ACM TMIS. https://doi.org/10.1145/3503488
20. Hoque, M. N., Mashiat, T., Ghai, B., et al. (2024). *The HaLLMark Effect: Supporting Provenance and Transparent Use of Large Language Models in Writing with Interactive Visualization.* ACM CHI. https://doi.org/10.1145/3613904.3641895
21. Pan, B., Stakhanova, N., Ray, S. (2023). *Data Provenance in Security and Privacy.* ACM Computing Surveys. https://doi.org/10.1145/3593294
22. Chen, W., Wang, H., Chen, J., et al. (2019). *TabFact: A Large-scale Dataset for Table-based Fact Verification.* arXiv. https://doi.org/10.48550/arXiv.1909.02164
23. Zhang, H., Wang, Y., Wang, S., et al. (2020). *Table Fact Verification with Structure-Aware Transformer.* EMNLP. https://doi.org/10.18653/v1/2020.emnlp-main.126
24. Ou, S., Liu, Y. (2022). *Learning to Generate Programs for Table Fact Verification via Structure-Aware Semantic Parsing.* ACL. https://doi.org/10.18653/v1/2022.acl-long.525
25. Gu, Z., Fan, R., Zhao, X., et al. (2022). *OpenTFV: An Open Domain Table-Based Fact Verification System.* ACM SIGMOD. https://doi.org/10.1145/3514221.3520163
26. Hu, X., Hong, Z., Guo, Z., et al. (2023). *Read it Twice: Towards Faithfully Interpretable Fact Verification by Revisiting Evidence.* arXiv. https://doi.org/10.48550/arXiv.2305.03507
27. Bukkapatnam, K. S., Mehta, S. (2026). *TabFaith: Benchmarking and Improving Structural Faithfulness in LLM Table Summarization.* arXiv. https://doi.org/10.18653/v1/2026.surgellm-1.21
28. Huang, L., Feng, X., Ma, W., et al. (2024). *Advancing Large Language Model Attribution through Self-Improving.* arXiv. https://doi.org/10.48550/arXiv.2410.13298
29. Hu, T.-Y., Koppula, H. S., Pouransari, H., et al. (2025). *Learning from Self Critique and Refinement for Faithful LLM Summarization.* arXiv. https://doi.org/10.48550/arXiv.2512.05387
30. Gao, T., Yen, H., Yu, J., et al. (2023). *Enabling Large Language Models to Generate Text with Citations (ALCE).* EMNLP. https://doi.org/10.48550/arXiv.2305.14627
31. Min, S., Krishna, K., Lyu, X., et al. (2023). *FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation.* EMNLP. https://doi.org/10.48550/arXiv.2305.14251
32. Chen, J., Zhang, Y., Zhang, Y., et al. (2025). *Generative Interfaces for Language Models.* arXiv. https://doi.org/10.48550/arXiv.2508.19227
33. Lee, K. (2025). *Towards a Working Definition of Designing Generative User Interfaces.* ACM DIS. https://doi.org/10.1145/3715668.3736365
34. Peng, Y.-H., Bigham, J. P., Wu, J. (2026). *Efficient Personalization of Generative User Interfaces.* arXiv. https://doi.org/10.48550/arXiv.2604.09876
35. Zhou, Y., Yuan, M., Xu, H., et al. (2026). *LEGOUI: Designing with UI-DSL Bricks to Balance Transparency and Controllability.* arXiv. https://doi.org/10.48550/arXiv.2608.04293
36. Dibia, V. C. (2023). *LIDA: Automatic Generation of Grammar-Agnostic Visualizations and Infographics using Large Language Models.* ACL. https://doi.org/10.18653/v1/2023.acl-demo.11
37. Ross, S., Martinez, F., Houde, S., et al. (2023). *The Programmer's Assistant: Conversational Interaction with a Large Language Model for Software Development.* ACM IUI. https://doi.org/10.1145/3581641.3584037
38. Xie, M., Xie, Y. (2026). *Software as Content: Dynamic Applications as the Human-Agent Interaction Layer.* arXiv. https://doi.org/10.48550/arXiv.2603.21334

---
*Reproducibility artifacts:* `search.py` (queries), `raw/openalex.json`, `raw/arxiv.json`, `raw/combined.json`, `verify.py`, `raw/included_verified.json` (canonical metadata for all 38). Generated 2026-10-07.
