#!/usr/bin/env python3
"""bioRxiv / medRxiv scan via api.biorxiv.org/details (date window, 100 per page) + local regex filter.

The bioRxiv API has no keyword search, so this script walks a date window and keeps records
whose title+abstract match EVERY --must regex (case-insensitive). Large windows are slow
(the API is rate limited); use --max-scan to cap and report the fraction scanned honestly.
Europe PMC (SRC:PPR) also indexes most bioRxiv/medRxiv preprints and is faster for keyword recall.

Example:
  python3 scripts/search-biorxiv-v0.1.py --server biorxiv --from 2025-01-01 --to 2025-01-07 \
      --must "extracellular vesicle|exosome" --must "muscle|myoblast|satellite cell" --max-scan 2000
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
URL = "https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"


def to_record(it: dict, server: str, query_id: str) -> dict:
    return C.make_record(
        server, source_query=query_id, raw_id=f"{it.get('doi')}v{it.get('version')}", doi=it.get("doi"),
        title=it.get("title"), abstract=it.get("abstract"), authors=(it.get("authors") or "").split("; "),
        journal=server, year=it.get("date"), pub_types=[it.get("type") or "preprint", it.get("category") or ""],
        is_preprint=True, url=f"https://www.{server}.org/content/{it.get('doi')}v{it.get('version')}")


def search(start: str, end: str, must: list[str], server: str = "biorxiv", max_scan: int = 5000,
           max_records: int = 200, query_id: str = "q1", email: str | None = None):
    pats = [re.compile(m, re.I) for m in must]
    cursor, scanned, total, hits, seen = 0, 0, None, [], set()
    while scanned < max_scan:
        j = C.http_json(URL.format(server=server, start=start, end=end, cursor=cursor), email=email)
        msg = (j.get("messages") or [{}])[0]
        total = int(msg.get("total", 0) or 0)
        coll = j.get("collection") or []
        if not coll:
            break
        for it in coll:
            scanned += 1
            text = f"{it.get('title', '')} {it.get('abstract', '')}"
            if it.get("doi") in seen:
                continue
            if all(p.search(text) for p in pats):
                seen.add(it.get("doi"))
                hits.append(to_record(it, server, query_id))
        cursor += len(coll)
        C.log(f"  {server}: scanned {scanned}/{total}, hits {len(hits)}")
        if cursor >= total or len(hits) >= max_records:
            break
    return {"total_in_window": total, "scanned": scanned,
            "fraction_scanned": round(scanned / total, 3) if total else None}, hits[:max_records]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="biorxiv", choices=["biorxiv", "medrxiv"])
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--must", action="append", required=True, help="regex; repeat for AND")
    ap.add_argument("--max-scan", type=int, default=5000)
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--query-id", default="q1")
    ap.add_argument("--out")
    ap.add_argument("--email")
    a = ap.parse_args()
    info, recs = search(a.start, a.end, a.must, a.server, a.max_scan, a.max, a.query_id, a.email)
    if a.out:
        C.write_jsonl(a.out, recs)
    s = C.emit_summary(a.server, a.query_id, f"{a.start}..{a.end} must={a.must}", info["total_in_window"], len(recs), a.out)
    print(f"# scanned {info['scanned']} of {info['total_in_window']} ({info['fraction_scanned']})", file=sys.stderr)
    return s


if __name__ == "__main__":
    main()
