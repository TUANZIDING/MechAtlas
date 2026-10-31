#!/usr/bin/env python3
"""Europe PMC REST search (journal articles + preprints) with cursorMark paging.

Europe PMC covers PubMed/MEDLINE plus PMC full text, Agricola and preprints (SRC:PPR from
bioRxiv, medRxiv, Research Square, SSRN, ...). Use it for recall beyond PubMed.

Example:
  python3 scripts/search-europepmc-v0.1.py --query '(TITLE_ABS:"extracellular vesicle*") AND SRC:PPR' --max 20
  --preprints-only appends AND SRC:PPR; --exclude-medline appends NOT SRC:MED.
"""
import argparse
import importlib.util
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
URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DB = "europepmc"


def to_record(r: dict, query_id: str) -> dict:
    authors = [a.get("fullName") for a in (r.get("authorList") or {}).get("author", []) if a.get("fullName")]
    ptypes = (r.get("pubTypeList") or {}).get("pubType", [])
    journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("isoabbreviation") or r.get("bookOrReportDetails", {}).get("publisher")
    src = r.get("source")
    return C.make_record(
        DB, source_query=query_id, raw_id=f"{src}:{r.get('id')}", pmid=r.get("pmid"), pmcid=r.get("pmcid"),
        doi=r.get("doi"), title=r.get("title"), abstract=r.get("abstractText"), authors=authors,
        journal=journal or (r.get("bookOrReportDetails") or {}).get("publisher"), year=r.get("pubYear"),
        pub_types=ptypes, language=r.get("language"), is_preprint=(src == "PPR"),
        url=f"https://europepmc.org/article/{src}/{r.get('id')}")


def search(query: str, max_records: int = 200, email: str | None = None, query_id: str = "q1"):
    cursor, recs, total = "*", [], None
    while True:
        page = min(1000, max(1, max_records - len(recs))) if max_records else 1
        j = C.http_json(URL, {"query": query, "format": "json", "resultType": "core",
                              "pageSize": page, "cursorMark": cursor, "email": C.contact_email(email)}, email=email)
        total = j.get("hitCount", 0)
        res = (j.get("resultList") or {}).get("result", [])
        if not max_records:
            break
        recs.extend(to_record(r, query_id) for r in res)
        C.log(f"  europepmc {query_id}: {len(recs)}/{min(total, max_records)}")
        nxt = j.get("nextCursorMark")
        if not res or not nxt or nxt == cursor or len(recs) >= max_records:
            break
        cursor = nxt
    return total, recs[:max_records]


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--preprints-only", action="store_true")
    ap.add_argument("--exclude-medline", action="store_true")
    a = ap.parse_args()
    q = a.query + (" AND SRC:PPR" if a.preprints_only else "") + (" NOT SRC:MED" if a.exclude_medline else "")
    total, recs = search(q, a.max, a.email, a.query_id)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary(DB, a.query_id, q, total, len(recs), a.out)


if __name__ == "__main__":
    main()
