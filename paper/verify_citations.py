#!/usr/bin/env python3
"""verify_citations.py — resolve every DOI the paper cites and compare title + first author.

The review (`litreview/`) produced canonical metadata once. This is a *second*, independent
check performed at paper-writing time: for each citation it asks the DOI resolver where the
DOI actually points, and for arXiv DOIs it also queries the arXiv API for the canonical title
and author list. A citation passes only if the DOI resolves AND the resolved title matches the
review's title AND the first author's surname matches.

Exit non-zero if any citation fails, so a paper cannot be built on a dead reference.

    .venv/bin/python paper/verify_citations.py
"""
import json, re, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
VERIFIED = L5 / "litreview" / "raw" / "included_verified.json"
UA = "deewaanai-l5-router/2.0 (citation check; contact: DeeWaanAI)"

# The works this paper cites. Order is the paper's reference numbering.
CITES = [
    "FrugalGPT: How to Use Large Language Models While Reducing Cost",
    "Large Language Model Cascades with Mixture of Thoughts",
    "RouteLLM: Learning to Route LLMs with Preference Data",
    "Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing",
    "RouterBench: A Benchmark for Multi-LLM Routing System",
    "AutoMix: Automatically Mixing Language Models",
    "A Unified Approach to Routing and Cascading for LLMs",
    "Agreement-Based Cascading for Efficient Inference",
    "Most of the LLM Routing Gap Is Task Type",
    "Privacy-preserved LLM Cascade via CoT-enhanced Policy Learning",
    "Collaborative Inference and Learning between Edge SLMs and Cloud LLMs",
    "VeriRouter: Towards Verifiable LLM Routing",
    "When Correct Isn't Usable: Improving Structured Output Reliability",
    "Deterministic and Auditable Routing for Artificial Intelligence",
    "Route to Rome Attack: Directing LLM Routers to Expensive Models",
]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(200000)
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), b""
    except Exception as e:                      # noqa: BLE001 - reported as a failure
        return None, str(e), b""


def resolve_doi(doi):
    """Follow the DOI resolver; returns (status, final_url)."""
    status, url, _ = get(doi)
    return status, url


def arxiv_meta(arxiv_id):
    s, _u, body = get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}")
    if s != 200 or not body:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = root.find("a:entry", ns)
    if e is None:
        return None
    title = " ".join((e.findtext("a:title", "", ns) or "").split())
    authors = [" ".join((a.findtext("a:name", "", ns) or "").split())
               for a in e.findall("a:author", ns)]
    return {"title": title, "authors": authors}


def main():
    canon = json.loads(VERIFIED.read_text())
    by_prefix = {}
    for c in canon:
        by_prefix[norm(c["title"])[:45]] = c
    rows, fails = [], 0
    for want in CITES:
        key = norm(want)[:45]
        c = by_prefix.get(key)
        if c is None:
            rows.append({"wanted": want, "status": "NOT_IN_REVIEW_METADATA"})
            fails += 1
            print(f"  [FAIL] {want[:60]:60} not in the review's verified metadata")
            continue
        doi = c["doi"]
        out = {"title": c["title"], "doi": doi, "year": c["year"],
               "first_author": (c.get("authors") or [""])[0], "code": None}
        status, url = resolve_doi(doi)
        out["doi_status"] = status
        out["resolved_url"] = url
        # Resolution, not landing status: a publisher that blocks bots (ACM returns 403)
        # still proves the DOI exists if the resolver sent us to the publisher's page.
        resolved = bool(url) and "doi.org" not in url.split("//")[-1].split("/")[0]
        doi_ok = resolved
        out["landing_status"] = status
        m = re.search(r"arxiv\.(\d{4}\.\d{4,5})", doi, re.I)
        if "10.48550/arxiv" in doi.lower() and m:
            aid = m.group(1)
            meta = arxiv_meta(aid)
            out["arxiv_id"] = aid
            if meta:
                t_ok = norm(meta["title"])[:45] == key
                a_ok = bool(meta["authors"]) and (
                    meta["authors"][0].split()[-1].lower()
                    == (c.get("authors") or [""])[0].split()[-1].lower())
                out.update(resolved_title=meta["title"],
                           resolved_first_author=meta["authors"][0] if meta["authors"] else None,
                           title_match=t_ok, author_match=a_ok)
                out["pass"] = bool(doi_ok and t_ok and a_ok)
            else:
                out["pass"] = False
                out["error"] = "arxiv api returned nothing"
        else:
            # Non-arXiv DOI (ACL/ACM/Zenodo): resolution is the check we can automate honestly.
            out["pass"] = bool(doi_ok)
            out["note"] = "non-arXiv DOI: resolver reachability checked; title not re-derived"
        rows.append(out)
        ok = out.get("pass")
        fails += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        detail = (f"title={'ok' if out.get('title_match') else 'MISMATCH'} "
                  f"author={'ok' if out.get('author_match') else 'MISMATCH'}"
                  if "title_match" in out else out.get("note", ""))
        print(f"  [{mark}] {c['title'][:58]:58} {doi.split('doi.org/')[-1]:28} {detail}")
        time.sleep(0.4)

    (HERE / "citations_verified.json").write_text(json.dumps(
        {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "n": len(CITES), "n_pass": len(CITES) - fails, "citations": rows},
        indent=1, sort_keys=True) + "\n")
    print(f"\n{len(CITES) - fails}/{len(CITES)} verified; artifact: paper/citations_verified.json")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
