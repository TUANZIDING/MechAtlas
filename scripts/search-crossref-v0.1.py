#!/usr/bin/env python3
"""Crossref /works search (query.bibliographic) with cursor paging.

Crossref ranks by relevance over all metadata, so "count_total" is not a meaningful hit count;
take the top --max records and rely on screening. Useful for DOI-only journals, books,
conference papers and posted content (preprints) that PubMed misses.

Example:
  python3 scripts/search-crossref-v0.1.py --query "extracellular vesicles irradiation skeletal muscle" --max 20 \
      --filter "from-pub-date:2015-01-01"
"""
import argparse
import importlib.util
import re
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
URL = "https://api.crossref.org/works"
DB = "crossref"


def to_record(it: dict, query_id: str) -> dict:
    authors = [" ".join(x for x in (a.get("family"), a.get("given")) if x) or a.get("name", "") for a in it.get("author", [])]
    year = None
    for k in ("published-print", "published-online", "issued", "posted", "created"):
        parts = (it.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "") or None
    return C.make_record(
        DB, source_query=query_id, raw_id=it.get("DOI"), doi=it.get("DOI"),
        title=(it.get("title") or [None])[0], abstract=abstract, authors=authors,
        journal=(it.get("container-title") or [None])[0] or it.get("publisher"), year=year,
        pub_types=[it.get("type")] if it.get("type") else [], language=it.get("language"),
        is_preprint=(it.get("type") == "posted-content" or it.get("subtype") == "preprint"),
        url=it.get("URL"))


def search(query: str, max_records: int = 200, email: str | None = None, query_id: str = "q1",
           extra_filter: str | None = None):
    params = {"query.bibliographic": query, "cursor": "*", "mailto": C.contact_email(email)}
    if extra_filter:
        params["filter"] = extra_filter
    recs, total = [], None
    while True:
        params["rows"] = max(0, min(1000, max_records - len(recs)))
        msg = C.http_json(URL, params, email=email)["message"]
        total = msg.get("total-results")
        if not max_records:
            break
        items = msg.get("items", [])
        recs.extend(to_record(i, query_id) for i in items)
        C.log(f"  crossref {query_id}: {len(recs)}/{max_records}")
        nxt = msg.get("next-cursor")
        if not items or not nxt or len(recs) >= max_records:
            break
        params["cursor"] = nxt
    return total, recs[:max_records]


def fetch_doi(doi: str, email: str | None = None) -> dict | None:
    try:
        return C.http_json(f"{URL}/{doi}", {"mailto": C.contact_email(email)}, email=email, retries=3)["message"]
    except C.HttpError as e:
        if e.status == 404:
            return None
        raise


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--filter", help='Crossref filter, e.g. "from-pub-date:2015-01-01,type:journal-article"')
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.email, a.query_id, a.filter)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary(DB, a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
