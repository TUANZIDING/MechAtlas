#!/usr/bin/env python3
"""OPTIONAL: Elsevier Scopus Search API adapter (needs ELSEVIER_API_KEY; many results also
need an institutional IP or ELSEVIER_INSTTOKEN). Embase has no public search API; export
RIS from embase.com manually if your institution subscribes.

Soochow University does not subscribe to Scopus/Embase (as of 2026-10); this adapter exists for
users who do. Never publish raw Scopus/Embase exports (Elsevier terms); publish identifiers only.

Example:
  ELSEVIER_API_KEY=... python3 importers/elsevier-api-v0.1.py --query 'TITLE-ABS-KEY("extracellular vesicle*" AND "skeletal muscle")' --max 25
"""
import argparse
import os
import importlib.util
import sys
from pathlib import Path


def _load(rel: str):
    """Import a versioned file by path (file names carry version numbers), cached in sys.modules."""
    p = Path(__file__).resolve().parent.parent / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = _load("scripts/common-v0.1.py")

URL = "https://api.elsevier.com/content/search/scopus"


def to_record(e: dict, query_id: str) -> dict:
    return C.make_record("scopus", source_query=query_id, raw_id=e.get("eid"), pmid=e.get("pubmed-id"), doi=e.get("prism:doi"),
                         title=e.get("dc:title"), abstract=e.get("dc:description"),
                         authors=[e.get("dc:creator")] if e.get("dc:creator") else [], journal=e.get("prism:publicationName"),
                         year=e.get("prism:coverDate"), pub_types=[e.get("subtypeDescription") or ""],
                         url=next((l.get("@href") for l in e.get("link", []) if l.get("@ref") == "scopus"), None))


def search(query: str, max_records: int = 25, query_id: str = "api"):
    key = os.environ.get("ELSEVIER_API_KEY")
    if not key:
        raise SystemExit("ELSEVIER_API_KEY is not set; export CSV/RIS manually instead")
    h = {"X-ELS-APIKey": key, "Accept": "application/json"}
    if os.environ.get("ELSEVIER_INSTTOKEN"):
        h["X-ELS-Insttoken"] = os.environ["ELSEVIER_INSTTOKEN"]
    recs, start, total = [], 0, None
    while len(recs) < max_records:
        j = C.http_json(URL, {"query": query, "count": min(25, max_records - len(recs)), "start": start}, headers=h)
        sr = j.get("search-results") or {}
        total = int(sr.get("opensearch:totalResults") or 0)
        ents = [e for e in sr.get("entry", []) if not e.get("error")]
        recs += [to_record(e, query_id) for e in ents]
        if not ents:
            break
        start += len(ents)
    return total, recs[:max_records]


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.query_id)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary("scopus", a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
