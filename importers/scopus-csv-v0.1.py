"""Scopus CSV export -> common records (columns: Authors, Title, Year, Source title, DOI,
Abstract, PubMed ID, Document Type, EID, Link...). Raw exports are Elsevier-licensed data:
keep in inputs/ (gitignored); publish only identifiers + judgments.
"""
import csv
import io
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

WOS = _load("importers/wos-v0.1.py")


def parse_records(raw, db: str = "scopus", query_id: str = "export") -> list[dict]:
    text = WOS.decode(raw)
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lstrip("\ufeff"): (v or "").strip() for k, v in r.items()}
        if not r.get("Title"):
            continue
        authors = [a.strip() for a in (r.get("Author full names") or r.get("Authors") or "").split(";") if a.strip()]
        ab = r.get("Abstract")
        if ab and ab.lower().startswith("[no abstract"):
            ab = None
        out.append(C.make_record(db, source_query=query_id, raw_id=r.get("EID") or None, pmid=r.get("PubMed ID") or None,
                                 doi=r.get("DOI") or None, title=r["Title"], abstract=ab, authors=authors,
                                 journal=r.get("Source title") or None, year=r.get("Year"),
                                 pub_types=[r.get("Document Type") or ""], language=r.get("Language of Original Document") or None,
                                 is_preprint=False, url=r.get("Link") or None))
    return out
