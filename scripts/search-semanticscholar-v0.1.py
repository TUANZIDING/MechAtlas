#!/usr/bin/env python3
"""Semantic Scholar Graph API bulk search (/paper/search/bulk, token paging).

Bulk search supports boolean syntax: + (AND), | (OR), - (NOT), "phrase", prefix*.
Unauthenticated calls share a public pool and are often throttled (HTTP 429); the script
retries with backoff. Set S2_API_KEY for a personal key.

Example:
  python3 scripts/search-semanticscholar-v0.1.py --query '("extracellular vesicles" | exosomes) + "skeletal muscle" + (irradiation | radiation)' --max 20
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path


def _load(rel: str):
    """Import a versioned file (e.g. scripts/common-v0.1.py) by path, cached in sys.modules."""
    p = Path(__file__).resolve().parent.parent / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = _load("scripts/common-v0.1.py")
URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
DB = "semanticscholar"
FIELDS = "title,abstract,externalIds,year,venue,publicationTypes,authors,url"


def to_record(p: dict, query_id: str) -> dict:
    ext = p.get("externalIds") or {}
    ptypes = p.get("publicationTypes") or []
    return C.make_record(
        DB, source_query=query_id, raw_id=p.get("paperId"), pmid=ext.get("PubMed"),
        pmcid=("PMC" + str(ext["PubMedCentral"])) if ext.get("PubMedCentral") else None, doi=ext.get("DOI"),
        title=p.get("title"), abstract=p.get("abstract"), authors=[a.get("name") for a in p.get("authors") or []],
        journal=p.get("venue") or None, year=p.get("year"), pub_types=ptypes,
        is_preprint=bool(ext.get("ArXiv")) and not ext.get("DOI"), url=p.get("url"))


def search(query: str, max_records: int = 200, email: str | None = None, query_id: str = "q1",
           year: str | None = None):
    headers = {"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else None
    params = {"query": query, "fields": FIELDS, "year": year}
    recs, total, token = [], None, None
    while True:
        if token:
            params["token"] = token
        j = C.http_json(URL, params, headers=headers, email=email, retries=8)
        total = j.get("total")
        if not max_records:
            break
        data = j.get("data") or []
        recs.extend(to_record(p, query_id) for p in data)
        C.log(f"  semanticscholar {query_id}: {len(recs)}/{min(total or 0, max_records)}")
        token = j.get("token")
        if not data or not token or len(recs) >= max_records:
            break
    return total, recs[:max_records]


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--year", help='year or range, e.g. "2015-" or "2018-2026"')
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.email, a.query_id, a.year)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary(DB, a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
