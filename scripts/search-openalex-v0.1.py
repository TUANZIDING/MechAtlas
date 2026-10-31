#!/usr/bin/env python3
"""OpenAlex works search with cursor paging (polite pool via mailto).

--query is matched against title+abstract (filter title_and_abstract.search) unless
--fulltext is given (search= across title, abstract and full text where available).
Extra OpenAlex filters can be appended with --filter, e.g. "from_publication_date:2015-01-01,type:article".

Since 2026 OpenAlex meters anonymous use per IP (a small shared daily budget); set
OPENALEX_API_KEY (free, https://help.openalex.org/api/authentication/) for reliable use.

Example:
  python3 scripts/search-openalex-v0.1.py --query '"extracellular vesicles" AND "skeletal muscle" AND irradiation' --max 20
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
URL = "https://api.openalex.org/works"
DB = "openalex"


def abstract_from_index(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = {}
    for word, idx in inv.items():
        for i in idx:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def to_record(w: dict, query_id: str) -> dict:
    ids = w.get("ids") or {}
    src = ((w.get("primary_location") or {}).get("source") or {})
    authors = [(a.get("author") or {}).get("display_name") for a in w.get("authorships", [])]
    return C.make_record(
        DB, source_query=query_id, raw_id=(w.get("id") or "").rsplit("/", 1)[-1], pmid=ids.get("pmid"),
        pmcid=(ids.get("pmcid") or "").rsplit("/", 1)[-1] or None, doi=w.get("doi"),
        title=w.get("title") or w.get("display_name"), abstract=abstract_from_index(w.get("abstract_inverted_index")),
        authors=authors, journal=src.get("display_name"), year=w.get("publication_year"),
        pub_types=[w.get("type")] if w.get("type") else [], language=w.get("language"),
        is_preprint=(w.get("type") == "preprint" or (w.get("type") == "posted-content")),
        url=w.get("id"))


def search(query: str, max_records: int = 200, email: str | None = None, query_id: str = "q1",
           extra_filter: str | None = None, fulltext: bool = False):
    params = {"per-page": 200, "cursor": "*", "mailto": C.contact_email(email),
              "api_key": os.environ.get("OPENALEX_API_KEY")}
    filt = [] if fulltext else [f"title_and_abstract.search:{query}"]
    if extra_filter:
        filt.append(extra_filter)
    if filt:
        params["filter"] = ",".join(filt)
    if fulltext:
        params["search"] = query
    recs, total = [], None
    while True:
        params["per-page"] = max(1, min(200, max_records - len(recs))) if max_records else 1
        j = C.http_json(URL, params, email=email)
        total = (j.get("meta") or {}).get("count")
        if not max_records:
            break
        res = j.get("results", [])
        recs.extend(to_record(w, query_id) for w in res)
        C.log(f"  openalex {query_id}: {len(recs)}/{min(total or 0, max_records)}")
        nxt = (j.get("meta") or {}).get("next_cursor")
        if not res or not nxt or len(recs) >= max_records:
            break
        params["cursor"] = nxt
    return total, recs[:max_records]


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--filter", help="extra OpenAlex filter string")
    ap.add_argument("--fulltext", action="store_true", help="use search= (title/abstract/fulltext) instead of title_and_abstract")
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.email, a.query_id, a.filter, a.fulltext)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary(DB, a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
