#!/usr/bin/env python3
"""Normalise a manual database export into the common record schema (JSONL).

Formats: wos (plain text or tab-delimited, auto), scopus-csv, ris (Scopus/Embase/any RIS),
cnki (RefWorks / EndNote / NoteExpress / RIS, auto) -- use --db to label the source.

Examples:
  python3 scripts/import-export-v0.1.py --format wos --db wos inputs/wos/savedrecs.txt --out runs/x/raw/wos-export.jsonl
  python3 scripts/import-export-v0.1.py --format ris --db embase inputs/embase/records.ris --out runs/x/raw/embase-export.jsonl
  python3 scripts/import-export-v0.1.py --format cnki --db cnki inputs/cnki/CNKI-refworks.txt --out runs/x/raw/cnki-export.jsonl

Raw exports from WoS / Scopus / Embase / CNKI are licensed content: keep them under inputs/
(gitignored) and never commit or publish them; publish identifiers + screening judgments only.
"""
import argparse
import importlib.util
import json
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
PARSERS = {
    "wos": "importers/wos-v0.1.py",
    "scopus-csv": "importers/scopus-csv-v0.1.py",
    "ris": "importers/ris-v0.1.py",
    "cnki": "importers/cnki-wanfang-v0.1.py",
}
DEFAULT_DB = {"wos": "wos", "scopus-csv": "scopus", "ris": "scopus", "cnki": "cnki"}


def import_files(fmt: str, files: list[str], db: str | None = None, query_id: str = "export") -> list[dict]:
    mod = _load(PARSERS[fmt])
    out = []
    for f in files:
        out += mod.parse_records(Path(f).read_bytes(), db or DEFAULT_DB[fmt], query_id)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--format", required=True, choices=sorted(PARSERS))
    ap.add_argument("--db", help="source label: wos | scopus | embase | cnki | wanfang | vip ...")
    ap.add_argument("--query-id", default="export")
    ap.add_argument("--query", default="", help="the exact query used in the database UI (logged)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    recs = import_files(a.format, a.files, a.db, a.query_id)
    C.write_jsonl(a.out, recs)
    C.emit_summary(a.db or DEFAULT_DB[a.format], a.query_id, a.query or f"manual export: {', '.join(a.files)}", len(recs), len(recs), a.out)


if __name__ == "__main__":
    main()
