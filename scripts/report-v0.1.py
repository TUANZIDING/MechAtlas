#!/usr/bin/env python3
"""Draft the Chinese report (conclusion first) from the run's data files.

Everything numeric is computed from the files; the agent then rewrites the "结论先说" bullets
in its own words, tagging each claim 【事实】/【关联】/【推测】, and never adds a number or a
reference that is not in these files.

Example:
  python3 scripts/report-v0.1.py --topic examples/ev-intervention/topic-v0.1.yaml \
     --registry examples/demo/registry-demo-v0.1.json --studies examples/demo/studies-demo-v0.1.json \
     --verify docs/demo/verify-refs-v0.1.json --validation docs/demo/validation-v0.1.json --out docs/demo/report-demo-v0.1.md
With --run-state the gate results and human approvals are added between
<!-- mechatlas:gates:begin --> / <!-- mechatlas:gates:end -->; pipeline-v0.1.py keeps that block in sync.
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


GATE_BEGIN, GATE_END = "<!-- mechatlas:gates:begin -->", "<!-- mechatlas:gates:end -->"
GSTATUS = {"pass": "通过", "pass_with_skips": "通过（有人工批准跳过的库）", "waived": "人工豁免", "escalated": "已上报人工",
           "approved": "已批准", "done": "完成", "disabled": "未启用", "pending": "未开始", "fail": "未通过",
           "awaiting_approval": "待审批", "reextract": "补提取中", "incomplete": "未完成", "running": "进行中"}
KIND = {"freeze": "(a) 冻结检索策略", "gate_exhausted": "(b) 闸门重试用尽", "changes": "(c) 证据改动", "release": "(d) 发布"}


def gates_section(st: dict) -> str:
    """Gate results and human approvals from run-state-v0.1.json. Deterministic (no 'now'), so re-syncing a
    report only changes it when the state really changed; the release checkpoint itself is not listed."""
    g, s = st.get("gates", {}), st.get("settings", {})
    gs = lambda k: GSTATUS.get((g.get(k) or {}).get("status"), (g.get(k) or {}).get("status") or "未运行")
    cps = [c for c in st.get("checkpoints", []) if c["kind"] != "release"]
    L = ["## 流程闸门与人工审批", "",
         f"外层循环：共 {st.get('outer_loop', 1)} 轮（上限 {s.get('max_outer_loops', 2)} 轮"
         + (f"，人工追加 {st['extra_loops_granted']} 轮" if st.get("extra_loops_granted") else "") + "）。"
         "闸门结果均来自 `run-state-v0.1.json`。", "",
         "| 闸门 | 结果 | 说明 |", "|---|---|---|"]
    fr = g.get("freeze") or {}
    L.append(f"| (a) 检索策略与纳排标准冻结 | {gs('freeze')} | {('批准人 ' + fr['approved_by']) if fr.get('approved_by') else ''} |")
    r = g.get("retrieval") or {}
    rs = s.get("retrieval", {})
    note = [f"自动重试 {r.get('retries_used', 0)}/{rs.get('max_retries', 3)} 次（退避 {'/'.join(str(x) for x in rs.get('backoff_seconds', []))} 秒）"]
    for db, d in sorted((r.get("decisions") or {}).items()):
        note.append(f"{db}：人工决定 {d['decision']}（{d.get('by')}{'，' + d['note'] if d.get('note') else ''}）")
    if r.get("failing"):
        note.append("仍失败：" + "、".join(f"{f['db']}/{f['query_id']}" for f in r["failing"]))
    L.append(f"| 检索（错误或 0 命中即重试） | {gs('retrieval')} | {'；'.join(note)} |")
    dd = r.get("dedupe") or {}
    if dd:
        L.append(f"| 去重（自动） | 完成 | 输入 {dd.get('input_records')} 条 → 去重后 {dd.get('unique_records')} 条 |")
    sc = g.get("screening") or {}
    L.append(f"| 筛查完整性 | {gs('screening')} | {sc.get('rows', '—')} 行，纳入 {sc.get('included', '—')} 篇 |")
    L.append(f"| 登记 schema 与证据契约（自动） | {gs('schema')} | |")
    cv = g.get("coverage") or {}
    res = cv.get("result") or {}
    if res:
        cat = "；".join(f"{k} {v}" for k, v in sorted((res.get("excused_by_category") or {}).items()))
        L.append(f"| 提取覆盖 | {gs('coverage')} | 纳入 {res['included']} 篇：进入证据边 {res['mapped']} 篇（{res['mapped_ratio']:.0%}，"
                 f"阈值 {s.get('coverage', {}).get('min_mapped_ratio', 0.5):.0%}），有记录理由未画边 {res['excused']} 篇"
                 + (f"（{cat}）" if cat else "") + f"，无对应 {res['unaccounted']} 篇，理由无效 {res['invalid']} 篇；"
                 f"本轮补提取 {cv.get('rounds_used', 0)}/{s.get('coverage', {}).get('max_reextract_rounds', 2)} 轮 |")
    ch = g.get("changes") or {}
    L.append(f"| (c) 证据改动审批 | {gs('changes')} | 已批准 {len(ch.get('approved', []))} 项，驳回 {len(ch.get('rejected', []))} 项 |")
    rd = g.get("render") or {}
    cnt = rd.get("counts") or {}
    rr = cnt.get("registry") or {}
    ra = cnt.get("atlas") or {}
    L.append(f"| 渲染一致性（逐条比对） | {gs('render')} | "
             + (f"登记表 节点 {rr.get('nodes')} / 关系 {rr.get('edges')} / 来源 {rr.get('sources')} = 图谱 节点 {ra.get('nodes')} / 关系 {ra.get('edges')}"
                f"（{ra.get('drawn_lines')} 条连线）/ 来源 {ra.get('sources')}；" if cnt else "")
             + f"构建 {len(rd.get('attempts', []))} 次，自动重排 {rd.get('relayouts_used', 0)}/{s.get('render', {}).get('max_relayout_attempts', 3)} 次；"
               f"浏览器核查 {rd.get('browser', '—')} |")
    ft = g.get("fulltext") or {}
    bl = ft.get("by_loop") or {}
    tried = sum(v.get("tried", 0) for v in bl.values())
    got = sum(len(v.get("retrieved", [])) for v in bl.values())
    up = sum(len(v.get("upgraded", [])) for v in bl.values())
    capn = len(ft.get("not_attempted_cap") or [])
    L.append(f"| 开放获取全文升级 | {gs('fulltext')} | 尝试 {tried} 篇仅摘要研究，取得全文 {got} 篇，重新提取后升级 {up} 篇"
             + (f"；因外层循环上限未尝试 {capn} 篇" if capn else "") + " |")
    rf = (g.get("refs") or {}).get("summary") or {}
    L.append(f"| 引用核查（自动） | {gs('refs')} | {rf.get('checked', '—')} 条：{json.dumps(rf.get('status', {}), ensure_ascii=False)} |")
    L += ["", "人工审批记录（Agent 不代为批准）：", ""]
    if not cps:
        L.append("- （无）")
    else:
        L += ["| 编号 | 类型 | 内容 | 决定 | 决定人 | 备注 |", "|---|---|---|---|---|---|"]
        for c in cps:
            rz = c.get("resolution") or {}
            dec = rz.get("decision") or "待定"
            if rz.get("items") and len(set(rz["items"].values())) > 1:
                dec = "；".join(f"{k}={v}" for k, v in rz["items"].items())
            L.append(f"| {c['id']} | {KIND.get(c['kind'], c['kind'])} | {c['title_zh']}（{len(c['items'])} 项） | {dec} | {rz.get('by', '')} | {rz.get('note') or ''} |")
    return "\n".join(L)


def gates_block(st: dict) -> str:
    return f"{GATE_BEGIN}\n{gates_section(st)}\n{GATE_END}"


def build(topic, reg, studies, prisma=None, verify=None, validation=None, run_state=None) -> str:
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
    unm = reg.get("unmapped") or []
    if unm:
        L += ["", f"纳入但未画证据边的研究（{len(unm)} 篇，均有记录理由）：", ""]
        L += [f"- {u.get('pmid') and 'PMID ' + str(u['pmid']) or u.get('doi') or u.get('id')}【{u.get('category')}】{zh(u.get('reason'))}" for u in unm]
    if run_state:
        L += ["", gates_block(run_state)]
    lim = [f"- 仅读摘要的纳入研究 {ab} 篇，相关关系只作条件性证据。",
           "- 分级反映模型相关性与验证强度，不等同人体临床因果；C 级与骨架节点仅供提出假说。",
           "- 订阅数据库（WoS/Scopus/Embase/CNKI）只能由用户手动导出后导入；未导入的库视为未检索。"]
    if run_state:
        r = (run_state.get("gates") or {}).get("retrieval") or {}
        skipped = [f"{db}（{d.get('note') or '人工决定跳过'}）" for db, d in sorted((r.get("decisions") or {}).items()) if d["decision"] == "skip"]
        if skipped:
            lim.append("- 检索失败且经人工决定跳过的库：" + "、".join(skipped) + "。")
        capn = len(((run_state.get("gates") or {}).get("fulltext") or {}).get("not_attempted_cap") or [])
        if capn:
            lim.append(f"- 受外层循环上限限制，{capn} 篇仅摘要研究未尝试获取开放全文。")
    L += ["", "## 6. 局限", ""] + lim + [""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--studies", required=True)
    ap.add_argument("--prisma")
    ap.add_argument("--verify")
    ap.add_argument("--validation")
    ap.add_argument("--run-state", help="run-state-v0.1.json from pipeline-v0.1.py: adds the gate / approval summary")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rd = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None
    text = build(C.load_topic(a.topic) if a.topic else None, rd(a.registry), rd(a.studies), rd(a.prisma), rd(a.verify), rd(a.validation),
                 rd(a.run_state))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"out": a.out, "chars": len(text)}))


if __name__ == "__main__":
    main()
