#!/usr/bin/env python3
"""Full-text upgrade pass: try to fetch open-access full text for included studies marked abstract_only.

Sources, in order (only open access; nothing behind a subscription is fetched):
  1. Europe PMC  search EXT_ID / DOI -> PMCID + isOpenAccess -> /{PMCID}/fullTextXML
  2. Unpaywall   /v2/{doi} -> best OA location (PDF or landing page); the PDF is downloaded when reachable

Writes fulltext/<key>.{xml,pdf} and a worklist JSON. Each retrieved study gets a proposal (abstract_only -> false,
grade review). The agent then reads the text, re-extracts, and records the outcome in the worklist item:
  "review": {"decision": "upgraded" | "kept_abstract_only", "reason": "...", "grade": "A|B|C"}
Grade changes still go through the human approval checkpoint (pipeline-v0.1.py, checkpoint "changes").
Re-running keeps every existing "review" and does not re-download retrieved items.

Example:
  python3 scripts/fulltext-oa-v0.1.py --studies runs/x/studies-v0.1.json --outdir runs/x/fulltext --out runs/x/fulltext-v0.1.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path


def _load(rel: str):
    p = Path(__file__).resolve().parent.parent / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = _load("scripts/common-v0.1.py")
G = _load("scripts/gates-v0.1.py")
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL = "https://api.unpaywall.org/v2/"


def _safe(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z.-]+", "-", key).strip("-")


def proposal(study: dict) -> dict:
    g = study.get("grade")
    p = {"abstract_only": "true → false（读完全文后确认）"}
    if g == "B":
        p["grade"] = "B → A 候选：仅当全文确认是目标课题的直接模型（见 grades.A 定义）"
    elif g == "C":
        p["grade"] = "C 保持；全文含目标模型直接测量时可提议 C → B/A"
    else:
        p["grade"] = f"{g} 保持；核对全文中的模型、剂量、时间点与因果检验"
    p["note"] = "等级改动须经人工审批（checkpoint changes）"
    return p


def try_one(study: dict, outdir: Path, email: str, get_json=None, get_bytes=None) -> dict:
    get_json = get_json or (lambda url, params=None: C.http_json(url, params, email=email, retries=3))
    get_bytes = get_bytes or (lambda url, params=None: C.http_get(url, params, email=email, retries=2))
    key = G.study_key(study)
    item = {"study": key, "title": study.get("title"), "grade": study.get("grade"), "status": "not_open_access",
            "source": None, "path": None, "url": None, "date": C.today()}
    try:
        q = f"EXT_ID:{study['pmid']} AND SRC:MED" if study.get("pmid") else f'DOI:"{study.get("doi")}"'
        j = get_json(f"{EPMC}/search", {"query": q, "format": "json", "resultType": "core", "pageSize": 1})
        hit = ((j.get("resultList") or {}).get("result") or [None])[0]
        if hit and hit.get("pmcid") and hit.get("isOpenAccess") == "Y":
            xml = get_bytes(f"{EPMC}/{hit['pmcid']}/fullTextXML")
            if xml and b"<body" in xml:
                p = outdir / f"{_safe(key)}.xml"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(xml)
                item.update(status="retrieved", source="europepmc", path=str(p), url=f"https://europepmc.org/article/PMC/{hit['pmcid']}")
                return item
        doi = study.get("doi") or (hit or {}).get("doi")
        if doi:
            u = get_json(UNPAYWALL + C.norm_doi(doi), {"email": email})
            loc = (u or {}).get("best_oa_location") or {}
            if u and u.get("is_oa") and (loc.get("url_for_pdf") or loc.get("url")):
                item.update(url=loc.get("url_for_pdf") or loc.get("url"), source="unpaywall", status="oa_link_only")
                if loc.get("url_for_pdf"):
                    try:
                        pdf = get_bytes(loc["url_for_pdf"])
                        if pdf and pdf[:4] == b"%PDF":
                            p = outdir / f"{_safe(key)}.pdf"
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_bytes(pdf)
                            item.update(status="retrieved", path=str(p))
                    except Exception as e:  # noqa: BLE001 -- keep the OA link, the agent can open it
                        item["note"] = f"PDF download failed: {str(e)[:120]}"
        if not hit and not doi:
            item["status"] = "not_found"
    except Exception as e:  # noqa: BLE001
        item.update(status="error", note=str(e)[:200])
    return item


def run(studies: list[dict], outdir: Path, old: list[dict] | None = None, email: str | None = None,
        only: set[str] | None = None, **fetchers) -> list[dict]:
    """Worklist for every abstract-only study; keeps earlier retrieved items and every agent review."""
    prev = {x["study"]: x for x in old or []}
    out = []
    for s in studies:
        key = G.study_key(s)
        if not s.get("abstract_only"):
            if key in prev:
                out.append(prev[key])
            continue
        if key in prev and (prev[key].get("status") == "retrieved" or (only is not None and key not in only)):
            out.append(prev[key])
            continue
        if only is not None and key not in only:
            continue
        item = try_one(s, outdir, C.contact_email(email), **fetchers)
        if item["status"] in ("retrieved", "oa_link_only"):
            item["proposal"] = proposal(s)
        if key in prev and prev[key].get("review"):
            item["review"] = prev[key]["review"]
        out.append(item)
    return out


def summary(items: list[dict]) -> dict:
    from collections import Counter
    return {"abstract_only": len(items), "status": dict(Counter(i["status"] for i in items)),
            "reviewed": sum(1 for i in items if i.get("review")),
            "upgraded": sum(1 for i in items if (i.get("review") or {}).get("decision") == "upgraded")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--studies", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--email")
    a = ap.parse_args()
    studies = json.loads(Path(a.studies).read_text(encoding="utf-8"))
    out = Path(a.out)
    old = json.loads(out.read_text(encoding="utf-8")).get("items") if out.exists() else None
    items = run(studies, Path(a.outdir), old, a.email)
    out.write_text(json.dumps({"summary": summary(items), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary(items), ensure_ascii=False))


if __name__ == "__main__":
    main()
