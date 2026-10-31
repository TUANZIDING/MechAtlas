#!/usr/bin/env python3
"""Citation snowballing (backward = references, forward = cited-by) from the included set.

Default backend is OpenAlex (needs OPENALEX_API_KEY for reliable use since the 2026 metering);
--backend s2 uses the Semantic Scholar Graph API instead (references / citations endpoints).

Stopping rule (AGENTS.md): run a round, screen the new candidates, add the newly included
studies to the seed file and run again; stop when a round yields fewer than 3 newly
INCLUDED studies. --auto-must lets the script iterate on its own using a regex pre-filter
as a provisional inclusion signal (machine triage only; humans/agents still screen).

Inputs:
  --seeds   file with one PMID or DOI per line, or a studies JSON (list with pmid/doi)
  --known   deduped JSONL of records already seen (excluded from the output)
Example:
  python3 scripts/snowball-openalex-v0.1.py --seeds examples/demo/studies-demo-v0.1.json --backend s2 \
      --max-per-seed 50 --out runs/demo/snowball-r1.jsonl
"""
import argparse
import importlib.util
import json
import os
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
OA = _load("scripts/search-openalex-v0.1.py")
S2 = _load("scripts/search-semanticscholar-v0.1.py")


def read_seeds(path: str) -> list[str]:
    p = Path(path)
    txt = p.read_text(encoding="utf-8")
    ids = []
    if p.suffix == ".json":
        data = json.loads(txt)
        data = data.get("studies", data) if isinstance(data, dict) else data
        for s in data:
            if s.get("doi"):
                ids.append("doi:" + C.norm_doi(s["doi"]))
            elif s.get("pmid"):
                ids.append("pmid:" + C.norm_pmid(s["pmid"]))
    else:
        for line in txt.split():
            if C.norm_doi(line):
                ids.append("doi:" + C.norm_doi(line))
            elif C.norm_pmid(line):
                ids.append("pmid:" + C.norm_pmid(line))
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------- OpenAlex ----
def oa_neighbours(seed: str, max_per_seed: int, email):
    key = os.environ.get("OPENALEX_API_KEY")
    kind, val = seed.split(":", 1)
    ext = f"https://doi.org/{val}" if kind == "doi" else f"pmid:{val}"
    try:
        w = C.http_json(f"https://api.openalex.org/works/{ext}", {"mailto": C.contact_email(email), "api_key": key}, email=email)
    except C.HttpError as e:
        if e.status == 404:
            return []
        raise
    out = []
    refs = [r.rsplit("/", 1)[-1] for r in w.get("referenced_works", [])][:max_per_seed]
    for i in range(0, len(refs), 50):
        j = C.http_json("https://api.openalex.org/works", {"filter": "openalex:" + "|".join(refs[i:i + 50]), "per-page": 50,
                                                          "mailto": C.contact_email(email), "api_key": key}, email=email)
        out += [OA.to_record(x, "snowball-back") for x in j.get("results", [])]
    wid = w["id"].rsplit("/", 1)[-1]
    j = C.http_json("https://api.openalex.org/works", {"filter": f"cites:{wid}", "per-page": min(200, max_per_seed),
                                                      "mailto": C.contact_email(email), "api_key": key}, email=email)
    out += [OA.to_record(x, "snowball-fwd") for x in j.get("results", [])]
    return out


# -------------------------------------------------- Semantic Scholar ----
def s2_neighbours(seed: str, max_per_seed: int, email):
    kind, val = seed.split(":", 1)
    pid = f"DOI:{val}" if kind == "doi" else f"PMID:{val}"
    headers = {"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else None
    out = []
    for rel, key, tag in (("references", "citedPaper", "snowball-back"), ("citations", "citingPaper", "snowball-fwd")):
        try:
            j = C.http_json(f"https://api.semanticscholar.org/graph/v1/paper/{pid}/{rel}",
                            {"fields": S2.FIELDS, "limit": min(1000, max_per_seed)}, headers=headers, email=email, retries=8)
        except C.HttpError as e:
            if e.status == 404:
                return out
            raise
        out += [S2.to_record(d[key], tag) for d in (j.get("data") or []) if d.get(key) and d[key].get("title")]
    return out


def keys_of(r: dict) -> set:
    k = set()
    if r.get("doi"):
        k.add("doi:" + r["doi"])
    if r.get("pmid"):
        k.add("pmid:" + str(r["pmid"]))
    if r.get("title"):
        k.add("t:" + C.norm_title(r["title"]))
    return k


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--known", help="JSONL of records already screened (deduped set)")
    ap.add_argument("--backend", choices=["openalex", "s2"], default="openalex")
    ap.add_argument("--max-per-seed", type=int, default=200)
    ap.add_argument("--auto-must", action="append", help="regex (repeat = AND) to iterate automatically")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--min-new", type=int, default=3, help="stop when a round adds fewer than this")
    ap.add_argument("--out", required=True)
    ap.add_argument("--email")
    a = ap.parse_args()

    seen = set()
    if a.known:
        for r in C.read_jsonl(a.known):
            seen |= keys_of(r)
    seeds = read_seeds(a.seeds)
    seen |= set(seeds)
    fn = oa_neighbours if a.backend == "openalex" else s2_neighbours
    new_all, rounds, errors = [], [], []
    frontier = seeds
    for rnd in range(1, (a.max_rounds if a.auto_must else 1) + 1):
        cand = []
        for s in frontier:
            try:
                for r in fn(s, a.max_per_seed, a.email):
                    if not (keys_of(r) & seen):
                        seen |= keys_of(r)
                        r["snowball_round"] = rnd
                        r["snowball_seed"] = s
                        cand.append(r)
            except C.HttpError as e:
                errors.append(f"{s}: HTTP {e.status}")
                C.log(f"  {s}: {e}")
        pats = [re.compile(p, re.I) for p in (a.auto_must or [])]
        passing = [r for r in cand if pats and all(p.search(f"{r.get('title') or ''} {r.get('abstract') or ''}") for p in pats)]
        rounds.append({"round": rnd, "seeds": len(frontier), "new_candidates": len(cand), "auto_pass": len(passing)})
        C.log(f"  round {rnd}: {len(frontier)} seeds -> {len(cand)} new candidates ({len(passing)} pass auto filter)")
        new_all += cand
        if not a.auto_must or len(passing) < a.min_new:
            break
        frontier = [("doi:" + r["doi"]) if r.get("doi") else ("pmid:" + r["pmid"]) for r in passing if r.get("doi") or r.get("pmid")]
    C.write_jsonl(a.out, new_all)
    print(json.dumps({"backend": a.backend, "seeds": len(seeds), "rounds": rounds, "new_candidates": len(new_all), "errors": errors,
                      "out": a.out, "date": C.today(),
                      "stop_rule": f"stop when a round yields < {a.min_new} newly included studies after screening"},
                     ensure_ascii=False))
    if errors and not new_all:
        sys.exit("all snowball requests failed (OpenAlex now needs OPENALEX_API_KEY; or use --backend s2)")


if __name__ == "__main__":
    main()
