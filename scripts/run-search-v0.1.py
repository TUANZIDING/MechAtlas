#!/usr/bin/env python3
"""One-command search runner driven by the topic YAML.

Runs every enabled database query in config -> raw JSONL per query, imports manual exports
listed under search.manual_exports, deduplicates, and writes/refreshes the screening sheet.
A failing database is logged (status "error") and the run continues; check the log.

Outputs in --run-dir (default runs/<topic.id>):
  raw/<db>-<query_id>.jsonl, search-log-v0.1.json, deduped-v0.1.jsonl,
  dedupe-stats-v0.1.json, screening-v0.1.csv

Examples:
  python3 scripts/run-search-v0.1.py examples/radiation-muscle/topic-v0.1.yaml --max 5 --only pubmed,europepmc
  python3 scripts/run-search-v0.1.py config/my-topic-v0.1.yaml             # full run, max_per_query from YAML
"""
import argparse
import importlib.util
import json
import sys
import time
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
ORDER = ["pubmed", "europepmc", "wos", "scopus", "embase", "openalex", "semanticscholar", "crossref",
         "biorxiv", "medrxiv", "cnki", "wanfang", "vip"]


def run_db(db: str, q: dict, cap: int, email: str):
    qid = q.get("id", "q1")
    mx = min(int(q.get("max", cap)), cap)
    if db == "pubmed":
        return _load("scripts/search-pubmed-v0.1.py").search(q["query"], mx, email, qid)
    if db == "europepmc":
        query = q["query"] + (" AND SRC:PPR" if q.get("preprints_only") else "") + (" NOT SRC:MED" if q.get("exclude_medline") else "")
        return _load("scripts/search-europepmc-v0.1.py").search(query, mx, email, qid)
    if db == "openalex":
        return _load("scripts/search-openalex-v0.1.py").search(q["query"], mx, email, qid, q.get("filter"), bool(q.get("fulltext")))
    if db == "crossref":
        return _load("scripts/search-crossref-v0.1.py").search(q["query"], mx, email, qid, q.get("filter"))
    if db == "semanticscholar":
        return _load("scripts/search-semanticscholar-v0.1.py").search(q["query"], mx, email, qid, q.get("year"))
    if db in ("biorxiv", "medrxiv"):
        info, recs = _load("scripts/search-biorxiv-v0.1.py").search(q["from"], q["to"], q["must"], db,
                                                                     int(q.get("max_scan", 5000)), mx, qid, email)
        return info["total_in_window"], recs
    raise ValueError(f"unknown database {db}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topic")
    ap.add_argument("--run-dir")
    ap.add_argument("--max", type=int, help="override max records per query (smoke tests)")
    ap.add_argument("--only", help="comma-separated database list")
    ap.add_argument("--skip-exports", action="store_true")
    a = ap.parse_args()
    cfg = C.load_topic(a.topic)
    tid = cfg["topic"]["id"]
    run = Path(a.run_dir or f"runs/{tid}")
    (run / "raw").mkdir(parents=True, exist_ok=True)
    email = C.contact_email(cfg.get("contact_email"))
    s = cfg.get("search", {})
    cap = a.max if a.max is not None else int(s.get("max_per_query", 2000))
    only = set(a.only.split(",")) if a.only else None
    log, files = [], []
    dbs = s.get("databases", {})
    for db in sorted(dbs, key=lambda d: ORDER.index(d) if d in ORDER else 99):
        conf = dbs[db] or {}
        if not conf.get("enabled", True) or (only and db not in only):
            continue
        for q in conf.get("queries", []) + conf.get("windows", []):
            qid = q.get("id", "q1")
            out = run / "raw" / f"{db}-{qid}.jsonl"
            t0 = time.time()
            try:
                total, recs = run_db(db, q, cap, email)
                C.write_jsonl(out, recs)
                files.append(out)
                entry = {"db": db, "query_id": qid, "query": q.get("query") or f"{q.get('from')}..{q.get('to')} must={q.get('must')}",
                         "count_total": total, "retrieved": len(recs), "status": "ok", "date": C.today(), "out": str(out),
                         "seconds": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001 -- keep going, record the failure
                entry = {"db": db, "query_id": qid, "query": q.get("query"), "count_total": None, "retrieved": 0,
                         "status": "error", "error": str(e)[:300], "date": C.today()}
            C.log(f"{db}/{qid}: {entry['status']} total={entry['count_total']} retrieved={entry['retrieved']}")
            log.append(entry)
    if not a.skip_exports:
        imp = _load("scripts/import-export-v0.1.py")
        for i, ex in enumerate(s.get("manual_exports", []) or []):
            paths = sorted(Path().glob(ex["path"]))
            if not paths:
                C.log(f"manual export not found (skipped): {ex['path']}")
                continue
            recs = imp.import_files(ex["format"], [str(p) for p in paths], ex.get("db"), ex.get("id", f"export{i + 1}"))
            out = run / "raw" / f"{ex.get('db', ex['format'])}-{ex.get('id', f'export{i + 1}')}.jsonl"
            C.write_jsonl(out, recs)
            files.append(out)
            log.append({"db": ex.get("db", ex["format"]), "query_id": ex.get("id", f"export{i + 1}"), "query": ex.get("query", ""),
                        "count_total": ex.get("count_total", len(recs)), "retrieved": len(recs), "status": "ok",
                        "date": ex.get("date", C.today()), "out": str(out), "manual_export": True})
    (run / "search-log-v0.1.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    dd = _load("scripts/dedupe-v0.1.py")
    recs = [r for f in files for r in C.read_jsonl(f)]
    merged, stats = dd.dedupe(recs)
    C.write_jsonl(run / "deduped-v0.1.jsonl", merged)
    (run / "dedupe-stats-v0.1.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    sh = _load("scripts/screen-sheet-v0.1.py")
    sheet = run / "screening-v0.1.csv"
    old = sh.read_sheet(sheet) if sheet.exists() else None
    rows = sh.make(merged, old, (cfg.get("screening") or {}).get("triage_must"))
    sh.write_sheet(sheet, rows)
    print(json.dumps({"topic": tid, "queries": len(log), "errors": [f"{e['db']}/{e['query_id']}" for e in log if e["status"] != "ok"],
                      "dedupe": stats, "sheet": str(sheet)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
