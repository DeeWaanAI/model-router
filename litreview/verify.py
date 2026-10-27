#!/usr/bin/env python3
"""Verify curated inclusions against OpenAlex (DOI) / arXiv API and emit
raw/included_verified.json with canonical metadata."""
import json, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))

# Curated selections: substring that uniquely identifies the title in screened.json
# or raw/arxiv.json. Order = reference numbering intent (by theme).
PICKS = [
    # --- routing & cascading core ---
    "FrugalGPT: How to Use Large Language Models While Reducing Cost",
    "Large Language Model Cascades with Mixture of Thoughts",
    "RouteLLM: Learning to Route LLMs with Preference Data",
    "Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing",
    "RouterBench: A Benchmark for Multi-LLM Routing System",
    "AutoMix: Automatically Mixing Language Models",
    "Large Language Model Routing with Benchmark Datasets",
    "A Unified Approach to Routing and Cascading for LLMs",
    "Efficient Contextual LLM Cascades through Budget-Constrained Policy Learning",
    "Agreement-Based Cascading for Efficient Inference",
    "Cost-Saving LLM Cascades with Early Abstention",
    "Rational Tuning of LLM Cascades via Probabilistic Modeling",
    "Adaptive LLM Routing under Budget Constraints",
    "Harnessing the Power of Multiple Minds: Lessons Learned from LLM Routing",
    "RouterEval: A Comprehensive Benchmark for Routing LLMs",
    "LLMRouterBench: A Massive Benchmark and Unified Framework for LLM Routing",
    "Arch-Router: Aligning LLM Routing with Human Preferences",
    "Most of the LLM Routing Gap Is Task Type",
    "ICL-Router: In-Context Learned Model Representations",
    # --- cost-quality frontier methods ---
    "C3PO: Optimized Large Language Model Cascades with Probabilistic Cost Constraints",
    "UCCI: Calibrated Uncertainty for Cost-Optimal LLM Cascade Routing",
    "Budget-Aware Calibrated Cascades for E-Commerce Customer Assistants",
    "SeqRoute: Global Budget-Aware Sequential LLM Routing",
    "Is Escalation Worth It? On the Depth of LLM Cascades",
    "Cost-Optimal LLM Routing with Limited User Feedback",
    "Routing Should Pay for Itself: Sparse Supervision",
    # --- privacy & local/cloud ---
    "Privacy-preserved LLM Cascade via CoT-enhanced Policy Learning",
    "Privacy-Preserving LLMs Routing",
    "A Hybrid Local-Cloud Large Language Model Architecture for Privacy Preservation",
    "OCELOT: Inference-Leakage Budgets",
    "Collaborative Inference and Learning between Edge SLMs and Cloud LLMs",
    "LLMSwitchBench: A New Edge-Cloud Routing Benchmark",
    "Efficient Inference for Edge Large Language Models: A Survey",
    "Sensitive-Topic Leakage Through LLM Routing Metadata",
    # --- capability & difficulty estimation ---
    "CSDiff: Training-Free LLM Task Routing via a CS Difficulty Lexicon",
    "Register Bias in Complexity-Based Large Language Model Routing",
    "Confidence-Calibrated Small-Large Language Model Collaboration",
    "Who Pays for Efficient Inference? Confidence-Based Small-to-Large LLM Routing",
    "SeLMRoute: Probabilistic Semantic Evidence for Large Language Model Routing",
    # --- verifiability, governance, security hazards ---
    "VeriRouter: Towards Verifiable LLM Routing",
    "When Correct Isn't Usable: Improving Structured Output Reliability in Small Language Models",
    "Research on Structured Output of Large Language Models Based on Dual-Dimensional Testing",
    "Deterministic and Auditable Routing for Artificial Intelligence in Regulated Environments",
    "Route to Rome Attack: Directing LLM Routers to Expensive Models",
    "When Efficiency Backfires: Cascading LLMs Trigger Cascade Failure under Adversarial Attack",
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "litreview-verify/1.0 (mailto:youssef@deewaanai.com)"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            if attempt == 3:
                print(f"  FAILED {url[:110]} -> {e}")
                return None
            time.sleep(2 * (attempt + 1))


def verify_openalex(doi):
    url = "https://api.openalex.org/works/" + urllib.parse.quote(doi) + "?mailto=youssef@deewaanai.com"
    raw = get(url)
    if not raw:
        return None
    w = json.loads(raw)
    return {
        "title": w.get("title"),
        "year": w.get("publication_year"),
        "venue": (w.get("primary_location") or {}).get("source") and
                 ((w["primary_location"]["source"] or {}).get("display_name")),
        "authors": [a["author"]["display_name"] for a in w.get("authorships", [])][:6],
        "doi": w.get("doi"),
        "cited_by": w.get("cited_by_count"),
        "verified_via": "openalex",
    }


def verify_arxiv(arxiv_id):
    base = arxiv_id.split("v")[0]
    url = "http://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(base)
    raw = get(url)
    if not raw:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(raw)
    e = root.find("a:entry", ns)
    if e is None:
        return None
    doi_el = e.find("arxiv:doi", ns)
    return {
        "title": " ".join(e.findtext("a:title", default="", namespaces=ns).split()),
        "year": int(e.findtext("a:published", default="2000", namespaces=ns)[:4]),
        "venue": "arXiv",
        "authors": [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)][:6],
        "doi": f"https://doi.org/10.48550/arXiv.{base}",
        "journal_doi": doi_el.text.strip() if doi_el is not None else None,
        "verified_via": "arxiv",
    }


def norm(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def main():
    corpus = json.load(open(os.path.join(HERE, "raw", "screened.json")))
    arxiv = json.load(open(os.path.join(HERE, "raw", "arxiv.json")))
    pool = corpus + arxiv
    verified, missing = [], []
    for pick in PICKS:
        cands = [r for r in pool if norm(pick)[:50] in norm(r.get("title"))]
        if not cands:
            missing.append(pick)
            print(f"MISS: {pick}")
            continue
        rec = cands[0]
        meta = None
        # prefer arXiv verification when we have an arxiv id, else OpenAlex by DOI
        if rec.get("arxiv_id"):
            meta = verify_arxiv(rec["arxiv_id"])
            time.sleep(3.1)
        if meta is None and rec.get("doi"):
            meta = verify_openalex(rec["doi"])
            time.sleep(0.3)
        if meta is None and rec.get("journal_doi"):
            meta = verify_openalex("https://doi.org/" + rec["journal_doi"])
        if meta is None:
            missing.append(pick)
            print(f"UNVERIFIED: {pick}")
            continue
        meta["pick"] = pick
        verified.append(meta)
        print(f"OK: {meta['year']} | {meta['title'][:80]}")
    json.dump(verified, open(os.path.join(HERE, "raw", "included_verified.json"), "w"), indent=1)
    print(f"\nverified={len(verified)} missing={len(missing)}")


if __name__ == "__main__":
    main()
