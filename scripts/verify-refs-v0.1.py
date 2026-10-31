#!/usr/bin/env python3
"""Resolve every PMID / DOI against live APIs and check the stored title matches.

- PMID -> NCBI esummary (batch of 200); also cross-checks the DOI that PubMed lists.
- DOI  -> Crossref /works/{doi}; a 404 falls back to a doi.org HEAD (DataCite, CNKI, etc.).
Status per item: OK | TITLE_MISMATCH | NOT_FOUND | DOI_PMID_CONFLICT | NO_ID | NETWORK_ERROR.

Accepts a studies JSON (list of {pmid, doi, title}), a registry JSON (uses "sources"),
or a JSONL of records. Exit code 1 if anything is not OK (NETWORK_ERROR tolerated with
--allow-network-fail, e.g. in CI).

Example:
  python3 scripts/verify-refs-v0.1.py examples/demo/registry-demo-v0.1.json --out docs/demo/verify-refs-v0.1.json
"""
import argparse
import difflib
import importlib.util
import json
import os
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
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def title_of(item) -> str | None:
    t = item.get("title")
    if isinstance(t, dict):
        return t.get("en") or t.get("original") or None
    return t


def load_items(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix == ".jsonl":
        return [{"id": r.get("id"), "pmid": r.get("pmid"), "doi": r.get("doi"), "title": r.get("title")} for r in C.read_jsonl(p)]
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "sources" in data:
        src = data["sources"]
        src = list(src.values()) if isinstance(src, dict) else src
        return [{"id": s.get("id"), "pmid": s.get("pmid"), "doi": s.get("doi"), "title": title_of(s)} for s in src]
    if isinstance(data, dict) and "studies" in data:
        data = data["studies"]
    return [{"id": s.get("id") or s.get("pmid") or s.get("doi"), "pmid": s.get("pmid"), "doi": s.get("doi"),
             "title": title_of(s)} for s in data]


def similar(a: str | None, b: str | None) -> float:
    a, b = C.norm_title(a), C.norm_title(b)
    if not a or not b:
        return 1.0
    if min(len(a), len(b)) >= 20 and (a in b or b in a):  # e.g. subtitle dropped
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def verify(items: list[dict], email=None, threshold=0.85) -> list[dict]:
    res = []
    pmids = sorted({C.norm_pmid(i["pmid"]) for i in items if i.get("pmid")})
    summ, net_err = {}, None
    for k in range(0, len(pmids), 200):
        try:
            j = C.http_json(ESUMMARY, {"db": "pubmed", "id": ",".join(pmids[k:k + 200]), "retmode": "json", "tool": "mechatlas",
                                       "email": C.contact_email(email), "api_key": os.environ.get("NCBI_API_KEY")}, email=email)
            summ.update({u: j["result"][u] for u in j.get("result", {}).get("uids", [])})
        except Exception as e:  # noqa: BLE001
            net_err = repr(e)
    crossref = _load("scripts/search-crossref-v0.1.py")
    for it in items:
        pmid, doi = C.norm_pmid(it.get("pmid")), C.norm_doi(it.get("doi"))
        r = {"id": it.get("id"), "pmid": pmid, "doi": doi, "status": "OK", "resolved_title": None, "similarity": None, "note": ""}
        if not pmid and not doi:
            r["status"] = "NO_ID"
            res.append(r)
            continue
        if pmid:
            s = summ.get(pmid)
            if s is None:
                r["status"] = "NETWORK_ERROR" if net_err else "NOT_FOUND"
                r["note"] = net_err or "PMID not in esummary"
            elif s.get("error"):
                r["status"], r["note"] = "NOT_FOUND", s["error"]
            else:
                r["resolved_title"] = s.get("title")
                pub_doi = next((C.norm_doi(a["value"]) for a in s.get("articleids", []) if a.get("idtype") == "doi"), None)
                if doi and pub_doi and pub_doi != doi:
                    r["status"], r["note"] = "DOI_PMID_CONFLICT", f"PubMed lists DOI {pub_doi}"
        if doi and r["status"] == "OK" and (not pmid or not r["resolved_title"]):
            try:
                m = crossref.fetch_doi(doi, email)
                if m:
                    r["resolved_title"] = (m.get("title") or [None])[0]
                else:
                    try:
                        C.http_get(f"https://doi.org/{doi}", method="HEAD", retries=2)
                        r["note"] = "resolved via doi.org (not in Crossref)"
                    except C.HttpError:
                        r["status"] = "NOT_FOUND"
            except C.HttpError as e:
                r["status"], r["note"] = "NETWORK_ERROR", str(e)[:200]
            except Exception as e:  # noqa: BLE001
                r["status"], r["note"] = "NETWORK_ERROR", repr(e)[:200]
        if r["status"] == "OK" and r["resolved_title"] and it.get("title"):
            r["similarity"] = round(similar(it["title"], r["resolved_title"]), 3)
            if r["similarity"] < threshold:
                r["status"] = "TITLE_MISMATCH"
        res.append(r)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out")
    ap.add_argument("--email")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--allow-network-fail", action="store_true")
    a = ap.parse_args()
    items = load_items(a.input)
    res = verify(items, a.email, a.threshold)
    from collections import Counter
    summary = {"checked": len(res), "status": dict(Counter(r["status"] for r in res)), "date": C.today(), "input": a.input}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"summary": summary, "items": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in res:
        if r["status"] != "OK":
            C.log(f"  {r['status']}: {r['id']} pmid={r['pmid']} doi={r['doi']} {r['note']} sim={r['similarity']}")
    print(json.dumps(summary, ensure_ascii=False))
    bad = [r for r in res if r["status"] not in ("OK",) and not (a.allow_network_fail and r["status"] == "NETWORK_ERROR")]
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
