"""Web of Science exports -> common records.

Supports the two WoS Core Collection export formats:
  * "Plain text file"  (tagged: PT/AU/TI/SO/AB/DI/PM/PY/UT ... ER)  -> parse_plain()
  * "Tab delimited file" (header row of the same tags; UTF-8 or UTF-16) -> parse_tab()
parse_records() detects the format. Raw exports are licensed Clarivate data: keep them in
inputs/ (gitignored) and publish only identifiers + screening judgments.
"""
import csv
import io
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


def decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw.lstrip("\ufeff")
    for enc in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            txt = raw.decode(enc)
            if "\x00" not in txt:
                return txt.lstrip("\ufeff")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _rec(f: dict, db: str, query_id: str) -> dict:
    g = lambda k: (f.get(k) or "").strip()
    ptype = {"J": "journal article", "B": "book", "S": "series", "P": "patent", "C": "conference"}.get(g("PT"), g("PT"))
    dts = [d.strip() for d in g("DT").split(";") if d.strip()]
    authors = [a.strip() for a in re.split(r"\n|;", f.get("AU") or "") if a.strip()]
    ut = g("UT")
    return C.make_record(db, source_query=query_id, raw_id=ut or None, pmid=g("PM") or None, doi=g("DI") or None,
                         title=g("TI").replace("\n", " "), abstract=g("AB").replace("\n", " ") or None, authors=authors,
                         journal=g("J9") or g("SO").replace("\n", " "), year=g("PY") or g("EA") or g("PD"),
                         pub_types=[ptype] + dts, language=g("LA") or None,
                         is_preprint=any("preprint" in d.lower() for d in dts),
                         url=f"https://www.webofscience.com/wos/woscc/full-record/{ut}" if ut else None)


def parse_plain(text: str, db: str = "wos", query_id: str = "export") -> list[dict]:
    out, cur, tag = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("ER"):
            if cur:
                out.append(_rec(cur, db, query_id))
            cur, tag = {}, None
            continue
        if line[:2] in ("FN", "VR", "EF") and not cur:
            continue
        if line.startswith("   ") and tag:
            cur[tag] += "\n" + line.strip()
            continue
        m = re.match(r"^([A-Z][A-Z0-9])(?: (.*))?$", line)
        if m:
            tag = m.group(1)
            cur[tag] = (m.group(2) or "").strip()
    if cur:
        out.append(_rec(cur, db, query_id))
    return out


def parse_tab(text: str, db: str = "wos", query_id: str = "export") -> list[dict]:
    rows = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [_rec({k.strip(): v for k, v in r.items() if k}, db, query_id) for r in rows if (r.get("TI") or "").strip()]


def parse_records(raw, db: str = "wos", query_id: str = "export") -> list[dict]:
    text = decode(raw)
    first = text.splitlines()[0] if text else ""
    if "\t" in first and re.search(r"\bTI\b", first):
        return parse_tab(text, db, query_id)
    return parse_plain(text, db, query_id)
