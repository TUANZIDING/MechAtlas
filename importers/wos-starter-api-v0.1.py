#!/usr/bin/env python3
"""OPTIONAL: Web of Science Starter API adapter (needs your own key in WOS_STARTER_KEY).

Institutional web access (e.g. Soochow University via WebVPN) cannot be used by agents:
there is no API behind it and automated scraping breaches Clarivate's terms. If your library
or you have a Starter API key (https://developer.clarivate.com/apis/wos-starter), set
WOS_STARTER_KEY and run, otherwise export records manually (docs/guide-wos-export-v0.1.md).
The Starter API returns metadata only (no abstracts).

Example:
  WOS_STARTER_KEY=... python3 importers/wos-starter-api-v0.1.py --query 'TS=("extracellular vesicle*" AND "skeletal muscle")' --max 50
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

URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"


def to_record(d: dict, query_id: str) -> dict:
    ids = d.get("identifiers") or {}
    src = d.get("source") or {}
    authors = [a.get("displayName") or a.get("wosStandard") for a in (d.get("names") or {}).get("authors", [])]
    return C.make_record("wos", source_query=query_id, raw_id=d.get("uid"), pmid=ids.get("pmid"), doi=ids.get("doi"),
                         title=d.get("title"), authors=authors, journal=src.get("sourceTitle"), year=src.get("publishYear"),
                         pub_types=d.get("types") or [], url=(d.get("links") or {}).get("record"))


def search(query: str, max_records: int = 50, query_id: str = "api", db: str = "WOS"):
    key = os.environ.get("WOS_STARTER_KEY")
    if not key:
        raise SystemExit("WOS_STARTER_KEY is not set; use a manual export instead (docs/guide-wos-export-v0.1.md)")
    recs, page, total = [], 1, None
    while len(recs) < max_records:
        j = C.http_json(URL, {"q": query, "db": db, "limit": min(50, max_records - len(recs)), "page": page},
                        headers={"X-ApiKey": key})
        total = (j.get("metadata") or {}).get("total")
        hits = j.get("hits") or []
        recs += [to_record(d, query_id) for d in hits]
        if not hits:
            break
        page += 1
    return total, recs[:max_records]


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.query_id)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary("wos", a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
