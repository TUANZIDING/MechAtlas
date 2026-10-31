#!/usr/bin/env python3
"""Draw docs/assets/pipeline-v0.1.svg (the README workflow figure) from code; no image model involved."""
import html
from pathlib import Path

STEPS = [("S1", "多库检索", "PubMed · EPMC · OpenAlex\nS2 · Crossref · bioRxiv", "#d1475c"),
         ("S1'", "手动导出导入", "WoS · Scopus · Embase\nCNKI · 万方", "#b5482b"),
         ("S2", "去重 + PRISMA", "DOI → PMID → 题名\n计数 · 流程图", "#b07d2a"),
         ("S3", "筛查（写理由）", "题名/摘要 → 全文\n仅摘要单独标记", "#13907d"),
         ("S4", "引文追溯", "参考文献 + 被引\n新增 <3 篇即停", "#2f9b74"),
         ("S5", "A/B/C 分级", "直接 · 邻近 · 推断\n矛盾/假说 → 骨架", "#2a8fb0"),
         ("S6", "机制登记 JSON", "泳道 · 节点 · 证据边\n方向/剂量/因果/局限", "#3d7fe0"),
         ("S7", "交互图谱 HTML", "由数据渲染 · 浅色\n点节点看证据", "#4a74c9"),
         ("S8", "核查", "PMID/DOI API 解析\nPlaywright 真实点击 · 390px", "#7a52c9"),
         ("S9", "中文报告", "结论先行\n事实 / 关联 / 推测", "#8a5cf0")]
W, BW, BH, GX, GY = 1200, 210, 112, 22, 70
font = "-apple-system,'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif"
out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} 400" width="{W}" height="400" font-family="{font}">',
       '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#8795a8"/></marker></defs>',
       '<rect width="100%" height="100%" fill="#ffffff"/>',
       '<text x="600" y="34" text-anchor="middle" font-size="19" font-weight="700" fill="#1d2b3a">课题 YAML → 可核查的机制通路图谱</text>']
x0 = (W - (5 * BW + 4 * GX)) / 2
for i, (sid, title, sub, col) in enumerate(STEPS):
    row, k = divmod(i, 5)
    k = k if row == 0 else 4 - k  # snake layout
    x, y = x0 + k * (BW + GX), 64 + row * (BH + GY)
    out.append(f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="14" fill="#ffffff" stroke="#d5dde8"/>')
    out.append(f'<rect x="{x}" y="{y + 12}" width="5" height="{BH - 24}" rx="2" fill="{col}"/>')
    out.append(f'<text x="{x + 20}" y="{y + 30}" font-size="12" font-weight="700" fill="{col}">{html.escape(sid)}</text>')
    out.append(f'<text x="{x + 20}" y="{y + 54}" font-size="17" font-weight="700" fill="#1d2b3a">{html.escape(title)}</text>')
    for j, ln in enumerate(sub.split("\n")):
        out.append(f'<text x="{x + 20}" y="{y + 77 + j * 18}" font-size="12.5" fill="#5d6b7c">{html.escape(ln)}</text>')
    if i < len(STEPS) - 1:
        if i == 4:
            out.append(f'<path d="M{x + BW / 2},{y + BH} L{x + BW / 2},{y + BH + GY - 4}" stroke="#8795a8" stroke-width="1.8" fill="none" marker-end="url(#a)"/>')
        elif row == 0:
            out.append(f'<path d="M{x + BW},{y + BH / 2} L{x + BW + GX - 3},{y + BH / 2}" stroke="#8795a8" stroke-width="1.8" marker-end="url(#a)"/>')
        else:
            out.append(f'<path d="M{x},{y + BH / 2} L{x - GX + 3},{y + BH / 2}" stroke="#8795a8" stroke-width="1.8" marker-end="url(#a)"/>')
out.append('<text x="600" y="384" text-anchor="middle" font-size="12.5" fill="#93a0b1">每个数字来自脚本输出 · 每篇文献来自本地保存的 API 记录 · 订阅库原始导出不公开</text>')
out.append("</svg>")
Path(__file__).resolve().parent.parent.joinpath("docs/assets/pipeline-v0.1.svg").write_text("\n".join(out), encoding="utf-8")
print("docs/assets/pipeline-v0.1.svg")
