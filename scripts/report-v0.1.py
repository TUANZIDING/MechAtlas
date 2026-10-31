#!/usr/bin/env python3
"""Draft the Chinese report (conclusion first) from the run's data files.

Everything numeric is computed from the files; the agent then rewrites the "结论先说" bullets
in its own words, tagging each claim 【事实】/【关联】/【推测】, and never adds a number or a
reference that is not in these files.

Example:
  python3 scripts/report-v0.1.py --topic examples/ev-intervention/topic-v0.1.yaml \
     --registry examples/demo/registry-demo-v0.1.json --studies examples/demo/studies-demo-v0.1.json \
     --verify docs/demo/verify-refs-v0.1.json --validation docs/demo/validation-v0.1.json --out docs/demo/report-demo-v0.1.md
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
zh = lambda o: (o.get("zh") or o.get("en") or "") if isinstance(o, dict) else (o or "")


def ref_tag(s):
    return f"PMID {s['pmid']}" if s.get("pmid") else f"DOI {s['doi']}"


def build(topic, reg, studies, prisma=None, verify=None, validation=None) -> str:
    srcs = {s["id"]: s for s in reg["sources"]}
    nodes = {n["id"]: n for n in reg["nodes"]}
    g = Counter(s.get("grade") for s in studies)
    ab = sum(1 for s in studies if s.get("abstract_only"))
    A_edges = [e for e in reg["edges"] if e["grade"] == "A"]
    causal = [e for e in reg["edges"] if e.get("causal")]
    scaf = [n for n in reg["nodes"] if n["status"] == "scaffold"]
    title = zh(topic["topic"]["title"]) if topic else zh(reg["title"])
    L = [f"# {title} · 机制图谱报告（{reg['version']}，{C.today()}）", ""]
    L += ["## 结论先说", "",
          "> 以下为脚本根据数据自动生成的事实要点；Agent 须改写为 3–5 条结论，每条标【事实】/【关联】/【推测】，不得引入文件之外的数字或文献。", "",
          f"- 【事实】纳入 {len(studies)} 篇（A {g.get('A', 0)} / B {g.get('B', 0)} / C {g.get('C', 0)}），其中仅读摘要 {ab} 篇。",
          f"- 【事实】图中 {len(reg['nodes'])} 个节点、{len(reg['edges'])} 条证据关系；A 级关系 {len(A_edges)} 条，做过因果验证的关系 {len(causal)} 条。"]
    if scaf:
        L.append("- 【事实】研究缺口（骨架节点，无证据边）：" + "；".join(zh(n["label"]) for n in scaf) + "。")
    L += ["", "## 1. 检索与筛选", ""]
    if prisma:
        L.append(_load("scripts/prisma-v0.1.py").to_markdown(prisma))
    else:
        L.append("（未提供 PRISMA 计数文件。）")
    if topic:
        dbs = topic.get("search", {}).get("databases", {})
        L += ["", "检索的数据库：" + "、".join(k for k, v in dbs.items() if (v or {}).get("enabled", True)) + "。",
              f"未检索或受限：{(topic.get('report') or {}).get('not_searched_note', '（未说明）')}"]
    L += ["", "## 2. 证据分级", "", "| 等级 | 定义 | 纳入篇数 |", "|---|---|---|"]
    for k, v in (reg.get("grades") or {}).items():
        L.append(f"| {k} | {zh(v.get('definition'))} | {g.get(k, 0)} |")
    L += ["", "## 3. 机制泳道与证据关系", ""]
    for lane in reg["lanes"]:
        es = [e for e in reg["edges"] if lane["r0"] <= nodes[e["source"]]["r"] < lane["r1"]]
        if not es:
            continue
        L += [f"### {lane['zh']}", "", "| 编号 | 关系 | 等级 | 因果验证 | 模型 | 文献 |", "|---|---|---|---|---|---|"]
        for e in es:
            arrow = "⊣" if e["direction"] == "-" else "→"
            refs = "；".join(ref_tag(srcs[r]) + ("（仅摘要）" if srcs[r].get("abstractOnly") else "") for r in e["refs"])
            L.append(f"| {e['id']} | {zh(nodes[e['source']]['label'])} {arrow} {zh(nodes[e['target']]['label'])} | {e['grade']} | "
                     f"{'是' if e.get('causal') else '否（关联）'} | {zh(e.get('model'))} | {refs} |")
        L.append("")
    if scaf:
        L += ["## 4. 研究缺口（骨架节点）", ""]
        L += [f"- {zh(n['label'])}：{zh(n.get('sub'))}" for n in scaf]
        L.append("")
    L += ["## 5. 核查", ""]
    if verify:
        L.append(f"- 文献核查（verify-refs）：{verify['summary']['checked']} 条，状态 {json.dumps(verify['summary']['status'], ensure_ascii=False)}；每个 PMID 经 NCBI esummary、DOI 经 Crossref 解析并比对题名。")
    if validation:
        L.append(f"- 图谱验证（Playwright 真实点击）：{validation['passed']}/{validation['passed'] + validation['failed']} 项通过；页面错误 {len(validation.get('pageErrors', []))} 条。")
    L += ["", "## 6. 局限", "",
          f"- 仅读摘要的纳入研究 {ab} 篇，相关关系只作条件性证据。",
          "- 分级反映模型相关性与验证强度，不等同人体临床因果；C 级与骨架节点仅供提出假说。",
          "- 订阅数据库（WoS/Scopus/Embase/CNKI）只能由用户手动导出后导入；未导入的库视为未检索。", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--studies", required=True)
    ap.add_argument("--prisma")
    ap.add_argument("--verify")
    ap.add_argument("--validation")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rd = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None
    text = build(C.load_topic(a.topic) if a.topic else None, rd(a.registry), rd(a.studies), rd(a.prisma), rd(a.verify), rd(a.validation))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"out": a.out, "chars": len(text)}))


if __name__ == "__main__":
    main()
