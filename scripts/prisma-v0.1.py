#!/usr/bin/env python3
"""PRISMA 2020-style counts, a markdown table and a simple flow-diagram SVG.

Inputs:
  --search-log   JSON list of search summaries (written by run-search-v0.1.py; one per query)
  --dedupe-stats JSON from dedupe-v0.1.py
  --sheet        screening CSV (screen-sheet-v0.1.py columns)
  --snowball     optional JSON summary(ies) from snowball-openalex-v0.1.py (citation searching)
Outputs: --out-json, --out-md, --out-svg (any subset)

Example:
  python3 scripts/prisma-v0.1.py --search-log runs/demo/search-log-v0.1.json --dedupe-stats runs/demo/dedupe-stats-v0.1.json \
      --sheet runs/demo/screening-v0.1.csv --out-md runs/demo/prisma-v0.1.md --out-svg runs/demo/prisma-v0.1.svg
"""
import argparse
import csv
import html
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


def counts(search_log: list[dict], dedupe_stats: dict, rows: list[dict], snowball: list[dict] | None = None) -> dict:
    by_db = Counter()
    for s in search_log:
        by_db[s["db"]] += int(s.get("retrieved") or 0)
    lo = lambda v: (v or "").strip().lower()
    ta_ex = [r for r in rows if lo(r.get("ta_decision")) == "exclude"]
    ta_pass = [r for r in rows if lo(r.get("ta_decision")) in ("include", "maybe")]
    not_ret = [r for r in ta_pass if lo(r.get("ft_decision")) == "not_retrieved"]
    ft_ex = [r for r in ta_pass if lo(r.get("ft_decision")) == "exclude"]
    inc = [r for r in rows if lo(r.get("ft_decision")) == "include"]
    pending = [r for r in rows if not lo(r.get("ta_decision"))]
    reasons = Counter((r.get("ft_reason") or "未写理由 / no reason").strip() for r in ft_ex)
    ta_reasons = Counter((r.get("ta_reason") or "未写理由 / no reason").strip() for r in ta_ex)
    grades = Counter((r.get("grade") or "?").strip().upper() for r in inc)
    sb = sum(int(s.get("new_candidates") or 0) for s in (snowball or []))
    return {
        "identified_by_database": dict(by_db),
        "identified_total": sum(by_db.values()),
        "identified_citation_searching": sb,
        "duplicates_removed": int(dedupe_stats.get("duplicates_removed", 0)),
        "records_screened": len(rows),
        "records_pending": len(pending),
        "records_excluded_title_abstract": len(ta_ex),
        "title_abstract_exclusion_reasons": dict(ta_reasons.most_common()),
        "reports_sought": len(ta_pass),
        "reports_not_retrieved": len(not_ret),
        "reports_assessed": len(ta_pass) - len(not_ret),
        "reports_excluded": len(ft_ex),
        "full_text_exclusion_reasons": dict(reasons.most_common()),
        "included": len(inc),
        "included_abstract_only": sum(1 for r in inc if lo(r.get("abstract_only")) in ("yes", "y", "true", "1")),
        "included_by_grade": dict(sorted(grades.items())),
        "date": C.today(),
    }


def to_markdown(c: dict) -> str:
    L = ["| 步骤 / Step | n |", "|---|---|"]
    for db, n in c["identified_by_database"].items():
        L.append(f"| 数据库检出 · {db} | {n} |")
    L += [f"| 检出合计 / identified | {c['identified_total']} |",
          f"| 引文追溯新增 / citation searching | {c['identified_citation_searching']} |",
          f"| 去重删除 / duplicates removed | {c['duplicates_removed']} |",
          f"| 题名摘要筛查 / screened | {c['records_screened']} |",
          f"| 未判定 / pending | {c['records_pending']} |",
          f"| 题名摘要排除 / excluded | {c['records_excluded_title_abstract']} |",
          f"| 拟获取全文 / reports sought | {c['reports_sought']} |",
          f"| 未获取全文 / not retrieved | {c['reports_not_retrieved']} |",
          f"| 全文评估 / assessed | {c['reports_assessed']} |",
          f"| 全文排除 / excluded | {c['reports_excluded']} |",
          f"| **纳入 / included** | **{c['included']}**（仅摘要 {c['included_abstract_only']}） |",
          f"| 分级 / by grade | {' · '.join(f'{k} {v}' for k, v in c['included_by_grade'].items()) or '—'} |"]
    if c["full_text_exclusion_reasons"]:
        L += ["", "全文排除理由 / full-text exclusion reasons:", ""]
        L += [f"- {k}: {v}" for k, v in c["full_text_exclusion_reasons"].items()]
    return "\n".join(L) + "\n"


def to_svg(c: dict) -> str:
    W, BW, BH = 860, 360, 74
    font = "-apple-system,'PingFang SC','Microsoft YaHei','WenQuanYi Micro Hei',sans-serif"
    boxes = []

    def box(x, y, lines, strong=False, w=BW):
        fill = "#eef6f4" if strong else "#ffffff"
        s = [f'<rect x="{x}" y="{y}" width="{w}" height="{BH}" rx="10" fill="{fill}" stroke="#9fb3c8"/>']
        for i, ln in enumerate(lines):
            s.append(f'<text x="{x + w / 2}" y="{y + 24 + i * 20}" text-anchor="middle" font-size="{13 if i == 0 else 12}" '
                     f'font-weight="{700 if i == 0 else 400}" fill="#1d2b3a">{html.escape(ln)}</text>')
        boxes.append("\n".join(s))

    def arrow(x1, y1, x2, y2):
        boxes.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#5d6b7c" stroke-width="1.6" marker-end="url(#a)"/>')

    dbs = ", ".join(f"{k} {v}" for k, v in c["identified_by_database"].items())
    lx, rx = 40, 460
    rows = [
        (["数据库检出 Identified", f"n = {c['identified_total']}", dbs[:52]],
         ["去重删除 Duplicates removed", f"n = {c['duplicates_removed']}", f"引文追溯 citation n = {c['identified_citation_searching']}"]),
        (["题名/摘要筛查 Screened", f"n = {c['records_screened']}", f"未判定 pending {c['records_pending']}"],
         ["排除 Excluded", f"n = {c['records_excluded_title_abstract']}", ""]),
        (["全文获取 Reports sought", f"n = {c['reports_sought']}", ""],
         ["未获取 Not retrieved", f"n = {c['reports_not_retrieved']}", ""]),
        (["全文评估 Assessed", f"n = {c['reports_assessed']}", ""],
         ["全文排除 Excluded with reasons", f"n = {c['reports_excluded']}",
          "; ".join(f"{k} {v}" for k, v in list(c["full_text_exclusion_reasons"].items())[:2])[:52]]),
    ]
    y = 30
    for i, (left, right) in enumerate(rows):
        box(lx, y, left)
        box(rx, y, right)
        arrow(lx + BW, y + BH / 2, rx - 4, y + BH / 2)
        arrow(lx + BW / 2, y + BH, lx + BW / 2, y + BH + 30)
        y += BH + 34
    g = " · ".join(f"{k} {v}" for k, v in c["included_by_grade"].items()) or "—"
    box(lx, y, ["纳入 Included", f"n = {c['included']}（仅摘要 abstract-only {c['included_abstract_only']}）", f"分级 {g}"], strong=True)
    H = y + BH + 30
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{font}">'
            f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M0 0L10 5L0 10Z" fill="#5d6b7c"/></marker></defs><rect width="100%" height="100%" fill="#ffffff"/>'
            + "\n".join(boxes) + "</svg>\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search-log", required=True)
    ap.add_argument("--dedupe-stats", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--snowball", action="append")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--out-svg")
    a = ap.parse_args()
    log = json.loads(Path(a.search_log).read_text(encoding="utf-8"))
    st = json.loads(Path(a.dedupe_stats).read_text(encoding="utf-8"))
    with open(a.sheet, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    sb = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.snowball or [])]
    c = counts(log, st, rows, sb)
    for path, text in ((a.out_json, json.dumps(c, ensure_ascii=False, indent=2)), (a.out_md, to_markdown(c) if a.out_md else None),
                       (a.out_svg, to_svg(c) if a.out_svg else None)):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding="utf-8")
    print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    main()
