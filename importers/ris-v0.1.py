"""RIS exports (Scopus, Embase/Ovid, CNKI, Wanfang, EndNote, Zotero) -> common records.

Tag mapping covers the RIS dialects of Scopus (T2 = source title, N1 may hold "PubMed ID"),
Embase.com (T1/A1/Y1/JF/N2, C5 or "PMID" mentions) and Chinese databases (UTF-8 or GB18030).
"""
import re
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
TAG = re.compile(r"^([A-Z][A-Z0-9])  -(?: (.*))?$")


def parse_raw(text: str) -> list[dict]:
    out, cur = [], {}
    last = None
    for line in text.splitlines():
        m = TAG.match(line.rstrip())
        if not m:
            if last and line.strip():
                cur[last][-1] += " " + line.strip()
            continue
        tag, val = m.group(1), (m.group(2) or "").strip()
        if tag == "ER":
            if cur:
                out.append(cur)
            cur, last = {}, None
            continue
        cur.setdefault(tag, []).append(val)
        last = tag
    if cur:
        out.append(cur)
    return out


def _first(f, *tags):
    for t in tags:
        if f.get(t) and f[t][0]:
            return f[t][0]
    return None


def find_pmid(f: dict, db: str):
    for vals in f.values():
        for v in vals:
            m = re.search(r"(?:PMID|PubMed ID|MEDLINE)[:\s]*(\d{1,8})\b", v, re.I)
            if m:
                return m.group(1)
    if db == "embase":
        v = _first(f, "C5")
        if v and re.fullmatch(r"\d{1,8}", v):
            return v
    return None


def to_record(f: dict, db: str, query_id: str) -> dict:
    authors = [a for t in ("AU", "A1", "A2") for v in f.get(t, []) for a in re.split(r";\s*", v) if a]
    ty = _first(f, "TY") or ""
    doi = _first(f, "DO") or next((v for v in f.get("UR", []) + f.get("L3", []) if "doi.org/" in v), None)
    return C.make_record(db, source_query=query_id, raw_id=_first(f, "AN", "U2", "ID", "M3") if db != "scopus" else _first(f, "AN", "ID"),
                         pmid=find_pmid(f, db), doi=doi, title=_first(f, "TI", "T1", "CT"),
                         abstract=_first(f, "AB", "N2"), authors=authors,
                         journal=_first(f, "T2", "JF", "JO", "JA", "J2") if ty != "BOOK" else _first(f, "PB"),
                         year=_first(f, "PY", "Y1", "DA"), pub_types=[ty] + f.get("M3", []),
                         language=_first(f, "LA"), is_preprint=any("preprint" in v.lower() for v in f.get("M3", []) + f.get("PT", [])),
                         url=_first(f, "UR", "L2"))


def parse_records(raw, db: str = "scopus", query_id: str = "export") -> list[dict]:
    return [to_record(f, db, query_id) for f in parse_raw(WOS.decode(raw)) if _first(f, "TI", "T1", "CT")]
