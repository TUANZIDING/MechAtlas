#!/usr/bin/env python3
"""MechAtlas gates: pure, offline checks used by scripts/pipeline-v0.1.py (stdlib only).

  retrieval   which databases / queries failed (error, 0 hits, manual export missing)
  coverage    every included study maps to >= 1 registry edge or carries an explicit recorded reason
  render      the built atlas HTML (embedded DATA) matches the registry one-to-one: nodes, edges, sources
  geometry    static layout check with the template's constants (node overlap, edge through node, node outside lane)
  diff        grade changes, edge deletions and node merges between an approved baseline and the current registry
  fingerprint stable hashes for the search-strategy freeze and the release

CLI (each prints JSON and exits 1 when the gate fails):
  python3 scripts/gates-v0.1.py retrieval --search-log runs/x/search-log-v0.1.json
  python3 scripts/gates-v0.1.py coverage  --registry R.json --studies S.json [--min-ratio 0.5]
  python3 scripts/gates-v0.1.py render    --registry R.json --html docs/index.html
  python3 scripts/gates-v0.1.py geometry  --registry R.json
  python3 scripts/gates-v0.1.py diff      --base-registry old.json --registry new.json [--base-studies … --studies …]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

# ------------------------------------------------------------ helpers ----


def norm_doi(d) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(d or "").strip().lower())


def study_key(s: dict) -> str:
    if s.get("pmid"):
        return f"pmid:{s['pmid']}"
    if s.get("doi"):
        return f"doi:{norm_doi(s['doi'])}"
    return str(s.get("id"))


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()[:16]


def file_fingerprint(*paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        p = Path(p)
        h.update(p.name.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def freeze_fingerprint(cfg: dict) -> str:
    """Search strategy + eligibility; changing any query, database switch or criterion needs a new approval."""
    s = dict(cfg.get("search") or {})
    s.pop("max_per_query", None)
    return fingerprint({"pico": cfg.get("pico"), "eligibility": cfg.get("eligibility"), "dates": cfg.get("dates"),
                        "languages": cfg.get("languages"), "search": s})


def _sources(reg: dict) -> list[dict]:
    s = reg.get("sources") or []
    return list(s.values()) if isinstance(s, dict) else s


# ---------------------------------------------------------- retrieval ----


def retrieval_failures(log: list[dict], fail_on_zero_hits: bool = True, excused: set[str] | None = None) -> list[dict]:
    """One item per failing (db, query). A database with 0 records over all its queries fails as a whole."""
    excused = excused or set()
    out = []
    per_db = Counter()
    seen_db = set()
    for e in log:
        seen_db.add(e["db"])
        per_db[e["db"]] += int(e.get("retrieved") or 0)
    for e in log:
        if e["db"] in excused:
            continue
        if e.get("status") != "ok":
            out.append({"db": e["db"], "query_id": e.get("query_id"), "reason": "error",
                        "detail": e.get("error") or e.get("status"), "manual": bool(e.get("manual_export"))})
    if fail_on_zero_hits:
        for db in sorted(seen_db - excused):
            if per_db[db] == 0:
                for e in log:
                    if e["db"] == db and e.get("status") == "ok":
                        out.append({"db": db, "query_id": e.get("query_id"), "reason": "zero_hits",
                                    "detail": "0 records over all queries of this database", "manual": bool(e.get("manual_export"))})
    return out


# ----------------------------------------------------------- coverage ----

UNMAPPED_CATEGORIES = {
    "background": "背景/综述性内容，无可画成边的机制读出",
    "redundant": "与已画边的研究为同一模型同一结论（需给出 see 来源卡）",
    "out-of-scope": "超出本图谱泳道范围（需说明属于哪个范围）",
    "scaffold": "只支持骨架节点（方向矛盾或假说；需给出 node）",
    "insufficient": "无法提取方向/对象明确的关系",
}
LAYOUT_WORDS = re.compile(r"布局|排版|几何|重叠|位置不够|空间不足|画不下|layout|geometry|overlap|no room|crowd", re.I)


def coverage(studies: list[dict], reg: dict, min_ratio: float = 0.5) -> dict:
    """Map every included study to edges; unmapped studies need registry.unmapped[] with a scientific reason."""
    srcs = _sources(reg)
    by_pmid = {str(s["pmid"]): s["id"] for s in srcs if s.get("pmid")}
    by_doi = {norm_doi(s["doi"]): s["id"] for s in srcs if s.get("doi")}
    edges_by_src: dict[str, list[str]] = {}
    for e in reg.get("edges") or []:
        for r in e.get("refs") or []:
            edges_by_src.setdefault(r, []).append(e["id"])
    node_ids = {n["id"] for n in reg.get("nodes") or []}
    scaffold = {n["id"] for n in reg.get("nodes") or [] if n.get("status") == "scaffold"}
    src_ids = {s["id"] for s in srcs}
    excuse = {}
    for u in reg.get("unmapped") or []:
        for k in ("pmid", "doi", "id"):
            if u.get(k):
                excuse[f"pmid:{u['pmid']}" if k == "pmid" else f"doi:{norm_doi(u['doi'])}" if k == "doi" else str(u["id"])] = u
    mapped, excused, unaccounted, invalid = [], [], [], []
    for s in studies:
        key = study_key(s)
        sid = (s.get("pmid") and by_pmid.get(str(s["pmid"]))) or (s.get("doi") and by_doi.get(norm_doi(s["doi"])))
        eids = edges_by_src.get(sid, []) if sid else []
        if eids:
            mapped.append({"study": key, "source": sid, "edges": eids})
            continue
        u = excuse.get(key) or excuse.get(str(s.get("id")))
        if not u:
            unaccounted.append({"study": key, "title": s.get("title"), "grade": s.get("grade"), "abstract_only": s.get("abstract_only")})
            continue
        reason = u.get("reason")
        text = " ".join(reason.values()) if isinstance(reason, dict) else str(reason or "")
        why = None
        if u.get("category") not in UNMAPPED_CATEGORIES:
            why = f"category must be one of {sorted(UNMAPPED_CATEGORIES)}"
        elif len(text.strip()) < 4:
            why = "reason is empty"
        elif LAYOUT_WORDS.search(text):
            why = "layout is never a reason to drop evidence: re-layout instead (relayout-v0.1.py)"
        elif u["category"] == "scaffold" and u.get("node") not in scaffold:
            why = "category scaffold needs node = an existing scaffold node id"
        elif u["category"] == "redundant" and u.get("see") not in src_ids:
            why = "category redundant needs see = a source card id that is drawn"
        if why:
            invalid.append({"study": key, "category": u.get("category"), "reason": text, "why": why})
        else:
            excused.append({"study": key, "category": u["category"], "reason": text, **({"node": u["node"]} if u.get("node") else {})})
    n = len(studies)
    ratio = round(len(mapped) / n, 3) if n else 1.0
    stray = [u for u in reg.get("unmapped") or [] if u.get("node") and u["node"] not in node_ids]
    return {"included": n, "mapped": len(mapped), "excused": len(excused), "unaccounted": len(unaccounted),
            "invalid": len(invalid), "mapped_ratio": ratio, "min_ratio": min_ratio,
            "excused_by_category": dict(Counter(x["category"] for x in excused)),
            "pass": not unaccounted and not invalid and not stray and ratio >= min_ratio,
            "unaccounted_items": unaccounted, "invalid_items": invalid, "excused_items": excused,
            "mapped_items": mapped, "stray_unmapped": stray}


# ------------------------------------------------------------- render ----


def atlas_data(html: str) -> dict:
    """The DATA object embedded by build-atlas-v0.1.mjs (`const DATA={...};`)."""
    i = html.find("const DATA=")
    if i < 0:
        raise ValueError("no `const DATA=` in atlas HTML")
    obj, _ = json.JSONDecoder().raw_decode(html[i + len("const DATA="):])
    if not isinstance(obj, dict):
        raise ValueError("atlas DATA is not an object (template not filled?)")
    return obj


def render_consistency(reg: dict, data: dict) -> dict:
    """Compare atlas DATA against the registry one-to-one. Grouping parallel relations into one drawn edge is fine;
    losing, duplicating, re-pointing or re-grading a relation is not."""
    mm = []
    rn = {n["id"]: n for n in reg.get("nodes") or []}
    dn_list = data.get("nodes") or []
    dn = {n["id"]: n for n in dn_list}
    for k, c in Counter(n["id"] for n in dn_list).items():
        if c > 1:
            mm.append(f"node {k} rendered {c} times")
    for k in sorted(set(rn) - set(dn)):
        mm.append(f"node {k} missing from atlas")
    for k in sorted(set(dn) - set(rn)):
        mm.append(f"node {k} in atlas but not in registry")
    for k in sorted(set(rn) & set(dn)):
        a, b = rn[k], dn[k]
        for f in ("status", "c", "r", "label"):
            if a.get(f) != b.get(f):
                mm.append(f"node {k}: {f} differs ({a.get(f)!r} vs {b.get(f)!r})")
    re_ = {e["id"]: e for e in reg.get("edges") or []}
    drawn = []
    for g in data.get("edges") or []:
        for it in g.get("items") or []:
            drawn.append((it.get("id"), g, it))
    for k, c in Counter(x[0] for x in drawn).items():
        if c > 1:
            mm.append(f"edge {k} rendered {c} times")
    for k in sorted(set(re_) - {x[0] for x in drawn}):
        e = re_[k]
        mm.append(f"edge {k} ({e['source']}>{e['target']}) missing from atlas")
    for k, g, it in drawn:
        e = re_.get(k)
        if not e:
            mm.append(f"edge {k} in atlas but not in registry")
            continue
        if (g.get("source"), g.get("target")) != (e["source"], e["target"]):
            mm.append(f"edge {k}: drawn {g.get('source')}>{g.get('target')}, registry {e['source']}>{e['target']}")
        if g.get("key") != f"{g.get('source')}>{g.get('target')}":
            mm.append(f"edge {k}: group key {g.get('key')} is not its endpoints")
        if it.get("sign") != e["direction"]:
            mm.append(f"edge {k}: direction {it.get('sign')} vs {e['direction']}")
        if it.get("grade") != e["grade"]:
            mm.append(f"edge {k}: grade {it.get('grade')} vs {e['grade']}")
        if list(it.get("refs") or []) != list(e.get("refs") or []):
            mm.append(f"edge {k}: refs differ")
        if bool(it.get("causal")) != bool(e.get("causal")):
            mm.append(f"edge {k}: causal differs")
    keys = Counter(g.get("key") for g in data.get("edges") or [])
    for k, c in keys.items():
        if c > 1:
            mm.append(f"edge group {k} drawn {c} times")
    rs = {s["id"] for s in _sources(reg)}
    ds = set((data.get("sources") or {}).keys()) if isinstance(data.get("sources"), dict) else {s["id"] for s in data.get("sources") or []}
    for k in sorted(rs ^ ds):
        mm.append(f"source {k} {'missing from atlas' if k in rs else 'in atlas but not in registry'}")
    st = data.get("stats") or {}
    if st.get("relations") != len(re_):
        mm.append(f"stats.relations {st.get('relations')} != registry edges {len(re_)}")
    if st.get("nodes") != len(rn):
        mm.append(f"stats.nodes {st.get('nodes')} != registry nodes {len(rn)}")
    return {"pass": not mm, "mismatches": mm, "registry": {"nodes": len(rn), "edges": len(re_), "sources": len(rs)},
            "atlas": {"nodes": len(dn_list), "edges": len(drawn), "drawn_lines": len(data.get("edges") or []), "sources": len(ds)}}


# ----------------------------------------------------------- geometry ----
# Same constants and routing as templates/atlas-template-v0.1.html (CW, RH, PADX, PADY, NW, NH; path()).
CW, RH, PADX, PADY, NW, NH = 232, 112, 150, 120, 192, 72


def _xy(n):
    return PADX + n["c"] * CW, PADY + n["r"] * RH


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def edge_points(a: dict, b: dict, k: int = 100):
    ax, ay = _xy(a)
    bx, by = _xy(b)
    if bx - ax > 0:
        s, t = (ax + NW, ay + NH / 2), (bx, by + NH / 2)
        c = max(20, (t[0] - s[0]) * 0.45)
        ctl = ((s[0] + c, s[1]), (t[0] - c, t[1]))
    else:
        s, t = (ax, ay + NH / 2), (bx, by + NH / 2)
        ctl = ((s[0] - 70, s[1]), (t[0] - 70, t[1]))
    return [_bezier(s, ctl[0], ctl[1], t, i / k) for i in range(1, k)]


def lane_of(reg: dict, r: float):
    return next((L for L in reg.get("lanes") or [] if L["r0"] <= r < L["r1"]), None)


def lane_range(L: dict) -> tuple[float, float]:
    """Rows a node may take inside lane L (the verifier's nodeOutsideLane rule, solved for r)."""
    return max(L["r0"], L["r0"] - 24 / RH), L["r1"] - (NH + 32) / RH


def geometry(reg: dict) -> dict:
    nodes = reg.get("nodes") or []
    byid = {n["id"]: n for n in nodes}
    out = {"nodeOverlap": [], "edgeThroughNode": [], "nodeOutsideLane": []}
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            (ax, ay), (cx, cy) = _xy(nodes[i]), _xy(nodes[j])
            if ax < cx + NW + 4 and cx < ax + NW + 4 and ay < cy + NH + 4 and cy < ay + NH + 4:
                out["nodeOverlap"].append(f"{nodes[i]['id']}/{nodes[j]['id']}")
    pairs = {(e["source"], e["target"]) for e in reg.get("edges") or [] if e["source"] in byid and e["target"] in byid}
    for s, t in sorted(pairs):
        for x, y in edge_points(byid[s], byid[t]):
            hit = next((n for n in nodes if n["id"] not in (s, t) and _xy(n)[0] - 3 < x < _xy(n)[0] + NW + 3
                        and _xy(n)[1] - 3 < y < _xy(n)[1] + NH + 3), None)
            if hit:
                out["edgeThroughNode"].append(f"{s}>{t} x {hit['id']}")
                break
    for n in nodes:
        L = lane_of(reg, n["r"])
        lo, hi = lane_range(L) if L else (0, -1)
        if not L or not (lo - 1e-9 <= n["r"] <= hi + 1e-9):
            out["nodeOutsideLane"].append(n["id"])
    return {"pass": not any(out.values()), **out}


# --------------------------------------------------------------- diff ----


def registry_diff(base_reg: dict | None, reg: dict, base_studies: list[dict] | None = None,
                  studies: list[dict] | None = None) -> list[dict]:
    """Items that need human approval (checkpoint c): grade changes, edge deletions/re-pointing, node merges/removals."""
    items = []
    for n in reg.get("nodes") or []:
        for old in n.get("mergedFrom") or []:
            items.append({"id": f"merge:node:{old}->{n['id']}", "kind": "node_merge", "node": n["id"], "from": old,
                          "zh": f"节点合并：{old} 并入 {n['id']}（mergedFrom）"})
    if base_reg:
        be = {e["id"]: e for e in base_reg.get("edges") or []}
        ce = {e["id"]: e for e in reg.get("edges") or []}
        bn = {n["id"] for n in base_reg.get("nodes") or []}
        cn = {n["id"] for n in reg.get("nodes") or []}
        explicit = {(i["from"], i["node"]) for i in items}
        for k in sorted(bn - cn):
            into = {ce[e][end] for e in set(be) & set(ce) for end in ("source", "target")
                    if be[e][end] == k and ce[e][end] != k}
            for y in sorted(into):
                if (k, y) not in explicit:
                    items.append({"id": f"merge:node:{k}->{y}", "kind": "node_merge", "node": y, "from": k,
                                  "zh": f"节点合并：{k} 的边改连到 {y}"})
            if not into and not any(f == k for f, _ in explicit):
                items.append({"id": f"remove:node:{k}", "kind": "node_removed", "node": k, "zh": f"删除节点 {k}"})
        for k in sorted(set(be) - set(ce)):
            e = be[k]
            items.append({"id": f"delete:edge:{k}", "kind": "edge_deletion", "edge": k,
                          "zh": f"删除证据边 {k}（{e['source']}→{e['target']}，{e['grade']} 级，文献 {', '.join(e.get('refs') or [])}）"})
        for k in sorted(set(be) & set(ce)):
            a, b = be[k], ce[k]
            if a["grade"] != b["grade"]:
                items.append({"id": f"grade:edge:{k}:{a['grade']}->{b['grade']}", "kind": "grade_change", "edge": k,
                              "from": a["grade"], "to": b["grade"], "zh": f"证据边 {k} 等级 {a['grade']} → {b['grade']}"})
            if (a["source"], a["target"]) != (b["source"], b["target"]) and not ({a["source"], a["target"]} - cn):
                items.append({"id": f"rewire:edge:{k}:{a['source']}>{a['target']}->{b['source']}>{b['target']}", "kind": "edge_rewired",
                              "edge": k, "zh": f"证据边 {k} 端点改为 {b['source']}→{b['target']}（原 {a['source']}→{a['target']}）"})
    if base_studies is not None and studies is not None:
        bs = {study_key(s): s for s in base_studies}
        for s in studies:
            o = bs.get(study_key(s))
            if o and o.get("grade") != s.get("grade"):
                items.append({"id": f"grade:study:{study_key(s)}:{o.get('grade')}->{s.get('grade')}", "kind": "grade_change",
                              "study": study_key(s), "from": o.get("grade"), "to": s.get("grade"),
                              "zh": f"纳入研究 {study_key(s)} 等级 {o.get('grade')} → {s.get('grade')}"
                                    + ("（已由仅摘要升级为全文）" if o.get("abstract_only") and not s.get("abstract_only") else "")})
    return items


# ---------------------------------------------------------------- CLI ----


def _read(p):
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "studies" in d and "nodes" not in d:
        return d["studies"]
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gate", choices=["retrieval", "coverage", "render", "geometry", "diff"])
    ap.add_argument("--search-log")
    ap.add_argument("--registry")
    ap.add_argument("--studies")
    ap.add_argument("--html")
    ap.add_argument("--min-ratio", type=float, default=0.5)
    ap.add_argument("--base-registry")
    ap.add_argument("--base-studies")
    a = ap.parse_args()
    if a.gate == "retrieval":
        items = retrieval_failures(_read(a.search_log))
        res = {"pass": not items, "failures": items}
    elif a.gate == "coverage":
        res = coverage(_read(a.studies), _read(a.registry), a.min_ratio)
    elif a.gate == "render":
        res = render_consistency(_read(a.registry), atlas_data(Path(a.html).read_text(encoding="utf-8")))
    elif a.gate == "geometry":
        res = geometry(_read(a.registry))
    else:
        items = registry_diff(_read(a.base_registry), _read(a.registry), _read(a.base_studies) if a.base_studies else None,
                              _read(a.studies) if a.studies else None)
        res = {"pass": not items, "needs_approval": items}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    sys.exit(0 if res["pass"] else 1)


if __name__ == "__main__":
    main()
