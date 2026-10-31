"""Chinese database exports (CNKI 知网, Wanfang 万方, VIP 维普) -> common records.

Formats:
  * RefWorks   (RT / A1 / T1 / JF / YR / AB / DO / K1 ..., records separated by blank lines)
  * EndNote    (%0 / %A / %T / %J / %D / %X / %R ...)
  * NoteExpress ({Reference Type}: / {Title}: / {Author}: / {Journal}: / {Year}: / {Abstract}: / {DOI}:)
parse_records(raw, db, fmt=None) auto-detects when fmt is None. Encoding: UTF-8 or GB18030.
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


def _split_authors(v: str) -> list[str]:
    return [a.strip() for a in re.split(r"[;；,，]\s*", v or "") if a.strip()]


def _mk(f: dict, db: str, query_id: str) -> dict:
    return C.make_record(db, source_query=query_id, raw_id=f.get("id"), doi=f.get("doi"), pmid=None,
                         title=f.get("title"), abstract=f.get("abstract"), authors=f.get("authors", []),
                         journal=f.get("journal"), year=f.get("year"), pub_types=[f.get("type") or ""],
                         language=f.get("language") or "zh", is_preprint=False, url=f.get("url"))


def parse_refworks(text: str, db: str = "cnki", query_id: str = "export") -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        f, authors = {}, []
        last = None
        for line in block.splitlines():
            m = re.match(r"^([A-Z][A-Z0-9]) (.*)$", line)
            if not m:
                if last and line.strip():
                    f[last] = f.get(last, "") + " " + line.strip()
                continue
            tag, val = m.group(1), m.group(2).strip()
            last = tag
            if tag in ("A1", "AU"):
                authors += _split_authors(val)
            else:
                f[tag] = val
        if f.get("T1"):
            out.append(_mk({"title": f.get("T1"), "authors": authors, "journal": f.get("JF") or f.get("JO"),
                            "year": f.get("YR") or f.get("FD"), "abstract": f.get("AB"), "doi": f.get("DO"),
                            "type": f.get("RT"), "url": f.get("LK") or f.get("UL"), "id": None,
                            "language": "zh" if re.search(r"[\u4e00-\u9fff]", f.get("T1", "")) else None}, db, query_id))
    return out


def parse_endnote(text: str, db: str = "cnki", query_id: str = "export") -> list[dict]:
    out = []
    for block in re.split(r"\n(?=%0 )", text.strip()):
        f, authors = {}, []
        for line in block.splitlines():
            m = re.match(r"^%(.) (.*)$", line)
            if not m:
                continue
            tag, val = m.group(1), m.group(2).strip()
            if tag == "A":
                authors += _split_authors(val)
            else:
                f.setdefault(tag, val)
        if f.get("T"):
            out.append(_mk({"title": f.get("T"), "authors": authors, "journal": f.get("J") or f.get("B"), "year": f.get("D"),
                            "abstract": f.get("X"), "doi": f.get("R"), "type": f.get("0"), "url": f.get("U"),
                            "language": "zh" if re.search(r"[\u4e00-\u9fff]", f.get("T", "")) else None}, db, query_id))
    return out


def parse_noteexpress(text: str, db: str = "wanfang", query_id: str = "export") -> list[dict]:
    out = []
    for block in re.split(r"\n(?=\{Reference Type\}:)", text.strip()):
        f = {}
        for m in re.finditer(r"^\{([^}]+)\}:\s*(.*)$", block, re.M):
            f.setdefault(m.group(1).strip(), m.group(2).strip())
        if f.get("Title"):
            out.append(_mk({"title": f.get("Title"), "authors": _split_authors(f.get("Author", "")),
                            "journal": f.get("Journal") or f.get("Secondary Title"), "year": f.get("Year"),
                            "abstract": f.get("Abstract"), "doi": f.get("DOI"), "type": f.get("Reference Type"),
                            "url": f.get("URL"),
                            "language": "zh" if re.search(r"[\u4e00-\u9fff]", f.get("Title", "")) else None}, db, query_id))
    return out


def detect(text: str) -> str:
    head = text.lstrip()[:400]
    if head.startswith("{Reference Type}"):
        return "noteexpress"
    if head.startswith("%0") or re.search(r"^%T ", head, re.M):
        return "endnote"
    if head.startswith("RT ") or re.search(r"^T1 ", head, re.M):
        return "refworks"
    if re.search(r"^TY  - ", head, re.M):
        return "ris"
    raise ValueError("unrecognised Chinese-database export format")


def parse_records(raw, db: str = "cnki", query_id: str = "export", fmt: str | None = None) -> list[dict]:
    text = WOS.decode(raw).replace("\r\n", "\n")
    fmt = fmt or detect(text)
    if fmt == "ris":
        return _load("importers/ris-v0.1.py").parse_records(text, db, query_id)
    return {"refworks": parse_refworks, "endnote": parse_endnote, "noteexpress": parse_noteexpress}[fmt](text, db, query_id)
