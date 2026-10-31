#!/usr/bin/env python3
"""Create (or refresh) the screening sheet CSV from deduped records, and export included studies.

Columns (one row per unique record):
  record_id, pmid, doi, year, journal, title, source_dbs, abstract,
  ta_decision (include|exclude|maybe), ta_reason,
  ft_decision (include|exclude|not_retrieved), ft_reason, abstract_only (yes|no), grade (A|B|C), lanes, notes, triage

Sub-commands:
  make     deduped JSONL -> screening CSV (keeps judgments already in an existing sheet)
           --must REGEX (repeatable) adds a machine "triage" hint only; it never decides.
  include  screening CSV -> studies JSON (only ft_decision=include), for prisma / atlas / verify
Examples:
  python3 scripts/screen-sheet-v0.1.py make runs/demo/deduped-v0.1.jsonl --out runs/demo/screening-v0.1.csv
  python3 scripts/screen-sheet-v0.1.py include runs/demo/screening-v0.1.csv --out runs/demo/studies-v0.1.json
"""
import argparse
import csv
import importlib.util
import json
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
COLS = ["record_id", "pmid", "doi", "year", "journal", "title", "source_dbs", "abstract", "ta_decision", "ta_reason",
        "ft_decision", "ft_reason", "abstract_only", "grade", "lanes", "notes", "triage"]
JUDGED = ["ta_decision", "ta_reason", "ft_decision", "ft_reason", "abstract_only", "grade", "lanes", "notes"]


def read_sheet(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_sheet(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM: opens cleanly in Excel / WPS
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make(records, old_rows=None, must=None, abstract_chars=1200):
    old = {r["record_id"]: r for r in (old_rows or [])}
    pats = [re.compile(m, re.I) for m in (must or [])]
    rows = []
    for r in records:
        text = f"{r.get('title') or ''} {r.get('abstract') or ''}"
        tri = ""
        if pats:
            tri = "topic-terms-present" if all(p.search(text) for p in pats) else "topic-terms-missing"
        if any("review" in (p or "").lower() for p in r.get("pub_types") or []):
            tri = (tri + ";review").strip(";")
        row = {"record_id": r["id"], "pmid": r.get("pmid") or "", "doi": r.get("doi") or "", "year": r.get("year") or "",
               "journal": r.get("journal") or "", "title": r.get("title") or "",
               "source_dbs": ";".join(r.get("source_dbs") or [r.get("source_db") or ""]),
               "abstract": (r.get("abstract") or "")[:abstract_chars], "triage": tri}
        for k in JUDGED:
            row[k] = (old.get(r["id"]) or {}).get(k, "")
        rows.append(row)
    return rows


def included(rows) -> list[dict]:
    out = []
    for r in rows:
        if (r.get("ft_decision") or "").strip().lower() == "include":
            out.append({"id": r["record_id"], "pmid": r.get("pmid") or None, "doi": r.get("doi") or None,
                        "year": C.parse_year(r.get("year")), "journal": r.get("journal") or None, "title": r.get("title"),
                        "grade": (r.get("grade") or "").strip().upper() or None,
                        "abstract_only": (r.get("abstract_only") or "").strip().lower() in ("yes", "y", "true", "1"),
                        "lanes": [x for x in re.split(r"[;,]", r.get("lanes") or "") if x],
                        "include_reason": r.get("ft_reason") or r.get("ta_reason") or None,
                        "notes": r.get("notes") or None})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("deduped")
    m.add_argument("--out", required=True)
    m.add_argument("--must", action="append")
    i = sub.add_parser("include")
    i.add_argument("sheet")
    i.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "make":
        old = read_sheet(a.out) if Path(a.out).exists() else None
        rows = make(C.read_jsonl(a.deduped), old, a.must)
        write_sheet(a.out, rows)
        print(json.dumps({"rows": len(rows), "kept_judgments": sum(1 for r in rows if r["ta_decision"]), "out": a.out}))
    else:
        studies = included(read_sheet(a.sheet))
        missing = [s["id"] for s in studies if s["grade"] not in ("A", "B", "C")]
        Path(a.out).write_text(json.dumps(studies, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"included": len(studies), "missing_grade": missing, "out": a.out}))


if __name__ == "__main__":
    main()
