#!/usr/bin/env python3
"""Dedupe + keyword-relevance screen for the Layer 5 litreview corpus."""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

POSITIVE = {
    # core topic terms (weighted 2)
    "routing": 2, "router": 2, "cascade": 2, "cascading": 2, "model selection": 2,
    "model routing": 3, "llm routing": 3, "offload": 2, "offloading": 2,
    "hybrid": 1, "on-device": 2, "edge-cloud": 2, "edge cloud": 2,
    # currencies
    "cost": 1, "price": 1, "budget": 1, "frugal": 2, "privacy": 2, "private": 1,
    "latency": 1, "quality": 1, "verifiab": 2, "structured output": 2,
    # context terms
    "large language model": 1, "llm": 1, "language model": 1, "small language model": 2,
    "query difficulty": 2, "task difficulty": 2, "capability": 1,
}
NEGATIVE = [
    # clearly off-topic domains
    "routing protocol", "network routing", "router network", "vehicle routing",
    "packet", "bgp", "wireless", "satellite", "uav", "drone", "5g", "6g",
    "drug", "protein", "molecular", "gene", "clinical", "medical image",
    "chip design", "fpga", "vlsi", "compiler", "pcb",
]


def score(rec):
    text = ((rec.get("title") or "") + " " + (rec.get("abstract") or "")).lower()
    if any(n in text for n in NEGATIVE):
        return 0
    s = 0
    for term, w in POSITIVE.items():
        if term in text:
            s += w
    return s


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def main():
    corpus = json.load(open(os.path.join(HERE, "raw", "combined.json")))
    arxiv = json.load(open(os.path.join(HERE, "raw", "arxiv.json")))
    seen_ids = {r["arxiv_id"].split("v")[0] for r in arxiv}
    corpus += [r for r in arxiv if r["arxiv_id"].split("v")[0] not in
               {c.get("arxiv_id", "").split("v")[0] for c in corpus}]

    by_doi, out = {}, []
    for r in corpus:
        doi = (r.get("doi") or "").lower()
        key = doi or ("t:" + norm_title(r.get("title")))
        if key in by_doi:
            continue
        by_doi[key] = True
        r["score"] = score(r)
        out.append(r)

    # merge journal DOI from arxiv records: drop openalex copies that duplicate an arxiv record
    out.sort(key=lambda r: -r["score"])
    screened = [r for r in out if r["score"] >= 6]
    with open(os.path.join(HERE, "raw", "screened.json"), "w") as f:
        json.dump(screened, f, indent=1)
    print(f"corpus={len(corpus)} deduped={len(out)} screened(score>=6)={len(screened)}")


if __name__ == "__main__":
    main()
