#!/usr/bin/env python3
"""Deduplicate records from several databases: DOI -> PMID -> normalised title (+year).

Records sharing any key are merged (union-find), so a record with DOI+PMID links a DOI-only
Crossref record and a PMID-only PubMed record. The merged record keeps the first non-empty
value per field (input order = priority, put PubMed first) and lists every hit in "hits".

Example:
  python3 scripts/dedupe-v0.1.py runs/demo/raw/*.jsonl --out runs/demo/deduped-v0.1.jsonl --stats runs/demo/dedupe-stats-v0.1.json
"""
import argparse
import importlib.util
import json
import sys
from collections import Counter
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
MIN_TITLE_LEN = 25  # very short normalised titles ("editorial") are not used as merge keys


def record_keys(r: dict, use_year: bool = True) -> list[str]:
    keys = []
    if r.get("doi"):
        keys.append("doi:" + C.norm_doi(r["doi"]))
    if r.get("pmid"):
        keys.append("pmid:" + str(C.norm_pmid(r["pmid"])))
    t = C.norm_title(r.get("title"))
    eff = len(t) + sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")  # CJK titles are denser: count CJK twice
    if eff >= MIN_TITLE_LEN:
        keys.append("t:" + t + (f"|{r.get('year')}" if use_year and r.get("year") else ""))
    return keys


def dedupe(records: list[dict], use_year: bool = False) -> tuple[list[dict], dict]:
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, r in enumerate(records):
        for k in record_keys(r, use_year):
            if k in owner:
                a, b = find(i), find(owner[k])
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[k] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(records)):
        groups.setdefault(find(i), []).append(i)
    merged = []
    for root in sorted(groups):
        idx = groups[root]
        m = {k: None for k in C.RECORD_FIELDS}
        for i in idx:
            for k, v in records[i].items():
                if k in m and m[k] in (None, [], "") and v not in (None, [], ""):
                    m[k] = v
        m["is_preprint"] = all(records[i].get("is_preprint") for i in idx)
        m["hits"] = [{"db": records[i].get("source_db"), "query": records[i].get("source_query"),
                      "raw_id": records[i].get("raw_id")} for i in idx]
        m["source_dbs"] = sorted({h["db"] for h in m["hits"] if h["db"]})
        m["id"] = f"doi:{m['doi']}" if m.get("doi") else f"pmid:{m['pmid']}" if m.get("pmid") else records[idx[0]]["id"]
        merged.append(m)
    by_db = Counter(r.get("source_db") for r in records)
    stats = {"input_records": len(records), "by_database": dict(by_db), "unique_records": len(merged),
             "duplicates_removed": len(records) - len(merged),
             "unique_only_in": dict(Counter(m["source_dbs"][0] for m in merged if len(m["source_dbs"]) == 1)),
             "date": C.today()}
    return merged, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="JSONL files in priority order")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats")
    ap.add_argument("--title-year", action="store_true", help="require matching year for title merges")
    a = ap.parse_args()
    recs = [r for f in a.inputs for r in C.read_jsonl(f)]
    merged, stats = dedupe(recs, a.title_year)
    C.write_jsonl(a.out, merged)
    if a.stats:
        Path(a.stats).write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
