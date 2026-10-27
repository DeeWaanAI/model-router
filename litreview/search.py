#!/usr/bin/env python3
"""Layer 5 (Model Router) literature review — programmatic search.

Queries OpenAlex (primary) and arXiv for LLM routing / cascading / cost-privacy-
verifiability-aware model selection literature, 2018-2026. Saves raw JSON
responses under raw/ for reproducibility.
"""
import json, os, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
os.makedirs(RAW, exist_ok=True)

WINDOW = ("2018-01-01", "2026-12-31")
PER_PAGE = 50
MAILTO = "youssef@deewaanai.com"

# Concept blocks -> query strings (OpenAlex title_and_abstract.search, arXiv abs/ti)
BLOCKS = {
    "routing_cascading": [
        "LLM routing model selection",
        "large language model cascade",
        "FrugalGPT",
        "RouteLLM",
        "routing between small and large language models",
    ],
    "cost_quality": [
        "cost-effective large language model inference",
        "cost quality tradeoff LLM",
        "frugal language model inference",
        "adaptive inference LLM budget",
    ],
    "privacy_local_cloud": [
        "privacy-aware LLM inference on-device cloud",
        "hybrid edge cloud large language model offloading",
        "local cloud language model privacy",
    ],
    "capability_difficulty": [
        "query difficulty prediction language model",
        "model capability estimation LLM benchmark prediction",
        "which model should answer language model selection",
    ],
    "small_large_delegation": [
        "small language model large language model collaboration",
        "LLM-as-judge model selection routing",
        "delegating queries smaller models LLM",
    ],
    "verifiability_structured": [
        "structured output reliability language model",
        "reasoning model structured output failure",
        "language model tabular data reliability",
    ],
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"litreview/1.0 (mailto:{MAILTO})"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 3:
                print(f"  FAILED {url[:120]} -> {e}")
                return None
            time.sleep(2 * (attempt + 1))


def openalex_search(q):
    params = {
        "search": q,
        "filter": f"from_publication_date:{WINDOW[0]},to_publication_date:{WINDOW[1]}",
        "per-page": PER_PAGE,
        "mailto": MAILTO,
        "sort": "relevance_score:desc",
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = get(url)
    if not data:
        return []
    out = []
    for w in data.get("results", []):
        out.append({
            "id": w.get("id"),
            "doi": w.get("doi"),
            "title": w.get("title"),
            "year": w.get("publication_year"),
            "venue": (w.get("primary_location") or {}).get("source", {}) and
                     ((w["primary_location"]["source"] or {}).get("display_name")),
            "cited_by": w.get("cited_by_count"),
            "abstract": invert_abstract(w.get("abstract_inverted_index")),
            "authors": [a["author"]["display_name"] for a in w.get("authorships", [])][:6],
            "source": "openalex",
        })
    return out


def invert_abstract(idx):
    if not idx:
        return ""
    pos = {}
    for word, places in idx.items():
        for p in places:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def arxiv_search(q):
    # arXiv API: all-field query quoted as phrase
    query = f'all:"{q}"'
    params = {
        "search_query": query,
        "start": 0, "max_results": PER_PAGE,
        "sortBy": "relevance",
    }
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    import xml.etree.ElementTree as ET
    req = urllib.request.Request(url, headers={"User-Agent": "litreview/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                root = ET.fromstring(r.read())
            break
        except Exception as e:
            if attempt == 3:
                print(f"  FAILED arxiv {q} -> {e}")
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
            "id": aid,
            "arxiv_id": arxiv_id,
            "doi": f"https://doi.org/10.48550/arXiv.{arxiv_id.split('v')[0]}",
            "journal_doi": doi_el.text.strip() if doi_el is not None else None,
            "title": " ".join(e.findtext("a:title", default="", namespaces=ns).split()),
            "year": int(published[:4]) if published else None,
            "venue": "arXiv",
            "cited_by": None,
            "abstract": " ".join(e.findtext("a:summary", default="", namespaces=ns).split()),
            "authors": [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)][:6],
            "source": "arxiv",
        })
    return out


def main():
    openalex_all, arxiv_all = [], []
    for block, queries in BLOCKS.items():
        for q in queries:
            print(f"[openalex] {block}: {q}")
            res = openalex_search(q)
            for r in res:
                r["block"] = block
                r["query"] = q
            openalex_all.extend(res)
            time.sleep(0.4)
            print(f"  -> {len(res)} records")
            print(f"[arxiv]    {block}: {q}")
            res = arxiv_search(q)
            for r in res:
                r["block"] = block
                r["query"] = q
            arxiv_all.extend(res)
            time.sleep(3.1)  # arXiv rate limit: <= 1 req / 3 s
            print(f"  -> {len(res)} records")
    with open(os.path.join(RAW, "openalex.json"), "w") as f:
        json.dump(openalex_all, f, indent=1)
    with open(os.path.join(RAW, "arxiv.json"), "w") as f:
        json.dump(arxiv_all, f, indent=1)
    combined = openalex_all + arxiv_all
    with open(os.path.join(RAW, "combined.json"), "w") as f:
        json.dump(combined, f, indent=1)
    print(f"\nTotal: openalex={len(openalex_all)} arxiv={len(arxiv_all)} combined={len(combined)}")


if __name__ == "__main__":
    main()
