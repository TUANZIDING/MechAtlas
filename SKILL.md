---
name: mechatlas
description: Turn a biomedical topic into a verified mechanism / signalling-pathway atlas. Use when the user asks for a systematic literature search on a mechanism ("X 通过哪些通路影响 Y", "做一张信号通路图", "系统检索 + 证据分级 + 通路图谱"), wants PRISMA counts, A/B/C evidence grading, a pathway registry JSON, an interactive light-theme atlas HTML, or a Chinese evidence report. Runs multi-database search (PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, bioRxiv + manual WoS/Scopus/Embase/CNKI exports) → dedupe → screening with reasons → grading → registry → atlas → API/Playwright verification → report. Never fabricates references.
---

# MechAtlas：课题 → 可核查的机制通路图谱

你负责调度；脚本负责检索、计数、构建与核查。**任何文献、数字、PMID/DOI 都只能来自本地保存的 API 记录**。完整规则见 [AGENTS.md](AGENTS.md)。

## 0. 准备
- 读取或创建课题配置：复制 `config/topic-template-v0.1.yaml` → `config/<topic>-v0.1.yaml`，填 PICO、纳入/排除、各库检索式、泳道、分级定义、`contact_email`。示例：`examples/radiation-muscle/`、`examples/ev-intervention/`。
- 先确认联网（Codex 默认关闭网络，见 `prompts/codex-prompt-v0.1.md`）：`python3 scripts/search-pubmed-v0.1.py --query "skeletal muscle" --max 1`。
- 已知关键文献召回测试：列 3–5 篇用户确认的关键文献，检索完成后它们必须在去重集中；不在就改检索式。

## 1. 阶段 → 命令

| 阶段 | 做什么 | 命令 | 产出（runs/<topic>/） |
|---|---|---|---|
| S1 检索 | 各库全部检索式；订阅库读手动导出 | `python3 scripts/run-search-v0.1.py config/<topic>-v0.1.yaml` | `raw/*.jsonl`、`search-log-v0.1.json` |
| S2 去重 | DOI → PMID → 规范化题名 | （run-search 自动）或 `scripts/dedupe-v0.1.py` | `deduped-v0.1.jsonl`、`dedupe-stats-v0.1.json` |
| S3 筛查 | 逐条题名/摘要判定并写理由；全文获取（Europe PMC / PMC OA）与全文判定 | 编辑 `screening-v0.1.csv`（`screen-sheet-v0.1.py make` 可刷新且保留判定） | `screening-v0.1.csv` |
| S4 引文追溯 | 纳入集的参考文献 + 被引；新增纳入 <3 篇即停 | `scripts/snowball-openalex-v0.1.py --seeds studies.json --known deduped.jsonl [--backend s2]` | `snowball-r*.jsonl` |
| S5 分级提取 | A/B/C、仅摘要标记、模型/剂量/时间点/因果检验 | `scripts/screen-sheet-v0.1.py include screening.csv --out studies-v0.1.json` | `studies-v0.1.json` |
| S6 机制登记 | 节点（泳道/列）、证据边（方向/等级/文献/模型/剂量/因果/局限）、骨架节点 | 按 `schema/registry-v0.1.schema.json` 编写 | `registry-v0.1.json` |
| S7 图谱 | 由 JSON 渲染单文件浅色交互页（不用图像模型） | `node scripts/build-atlas-v0.1.mjs --registry … --studies … --out docs/<topic>.html` | HTML |
| S8 核查 | 引用解析、证据契约、真实点击、390 px | `verify-refs-v0.1.py`、`validate-registry-v0.1.py`、`verify-atlas-v0.1.mjs`、`shots-atlas-v0.1.mjs` | `verify-refs`、`validation` JSON、截图 |
| S9 报告 | PRISMA + 中文报告，结论先行 | `prisma-v0.1.py`、`report-v0.1.py` | `prisma-v0.1.{md,svg}`、`report-v0.1.md` |

## 2. 闸门（不过不前进）
- S3 之前：`search-log` 中无 `status: error`，或已在报告中写明哪个库失败、为何未补。
- S7 之前：`validate-registry-v0.1.py --registry … --studies …` 输出 `"ok": true`。
- 交付之前：`verify-refs` 全部 `OK`；`verify-atlas` 全部 PASS 且 `pageErrors: []`；截图逐张亲眼看过。

## 3. 交付
中文报告：**结论先说**（3–5 条，每条标【事实】/【关联】/【推测】）→ 检索与 PRISMA → 分级分布 → 各泳道证据表 → 研究缺口 → 核查结果 → 局限（未检索的库、仅摘要篇数）。只发布标识符与筛查判定，不发布 WoS/Scopus/Embase/CNKI 原始导出。
