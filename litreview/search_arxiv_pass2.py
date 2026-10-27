#!/usr/bin/env python3
"""Supplementary arXiv pass with short phrase queries (the main pass's long
phrases over all: returned ~nothing). Merges into raw/arxiv.json."""
import json, os, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW = ("2018-01-01", "2026-12-31")

QUERIES = {
    "routing_cascading": [
        '"LLM routing"', '"model routing" "language model"', '"RouteLLM"',
        '"LLM cascade"', '"FrugalGPT"', '"hybrid LLM"',
    ],
    "cost_quality": [
        '"cost-efficient" "large language model" inference', '"cost-optimal" LLM',
        '"price-performance" LLM',
    ],
    "privacy_local_cloud": [
        '"on-device" "cloud" "large language model"', '"edge-cloud" LLM offloading',
        '"privacy" LLM routing', '"local model" "cloud model" LLM privacy',
    ],
    "capability_difficulty": [
        '"query difficulty" LLM', '"task difficulty" "model selection" LLM',
        '"predict" "model performance" LLM routing',
    ],
    "small_large_delegation": [
        '"small language model" "large language model" routing',
        '"weak model" "strong model" LLM cascade', '"SLM" "LLM" collaboration',
    ],
    "verifiability_structured": [
        '"structured output" "reasoning model" failure',
        '"tabular" "reasoning" "language model" failure',
        '"constrained decoding" reliability',
    ],
}


def arxiv_search(q):
    params = {"search_query": f"all:{q}", "start": 0, "max_results": 50, "sortBy": "relevance"}
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "litreview/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                root = ET.fromstring(r.read())
            break
        except Exception as e:
            if attempt == 3:
                print(f"  FAILED {q} -> {e}")
                return []
            time.sleep(2 * (attempt + 1))
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for e in root.findall("a:entry", ns):
        published = e.findtext("a:published", default="", namespaces=ns)[:10]
        if published and not (WINDOW[0] <= published <= WINDOW[1]):
            continue
        aid = e.findtext("a:id", default="", namespaces=ns)
        arxiv_id = aid.rsplit("/", 1)[-1]
        doi_el = e.find("arxiv:doi", ns)
        out.append({
            "id": aid, "arxiv_id": arxiv_id,
            "doi": f"https://doi.org/10.48550/arXiv.{arxiv_id.split('v')[0]}",
            "journal_doi": doi_el.text.strip() if doi_el is not None else None,
            "title": " ".join(e.findtext("a:title", default="", namespaces=ns).split()),
            "year": int(published[:4]) if published else None,
            "venue": "arXiv", "cited_by": None,
            "abstract": " ".join(e.findtext("a:summary", default="", namespaces=ns).split()),
            "authors": [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)][:6],
            "source": "arxiv",
        })
    return out


def main():
    existing = json.load(open(os.path.join(HERE, "raw", "arxiv.json")))
    seen = {r["arxiv_id"].split("v")[0] for r in existing}
    added = 0
    for block, qs in QUERIES.items():
        for q in qs:
            res = arxiv_search(q)
            print(f"[arxiv] {block}: {q} -> {len(res)}")
            for r in res:
                key = r["arxiv_id"].split("v")[0]
                if key not in seen:
                    seen.add(key)
                    r["block"] = block
                    r["query"] = q
                    existing.append(r)
                    added += 1
            time.sleep(3.1)
    json.dump(existing, open(os.path.join(HERE, "raw", "arxiv.json"), "w"), indent=1)
    print(f"added {added}, arxiv total {len(existing)}")


if __name__ == "__main__":
    main()
