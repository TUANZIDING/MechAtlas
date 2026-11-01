---
name: mechatlas
description: Turn a biomedical topic into a verified mechanism / signalling-pathway atlas. Use when the user asks for a systematic literature search on a mechanism ("X 通过哪些通路影响 Y", "做一张信号通路图", "系统检索 + 证据分级 + 通路图谱"), wants PRISMA counts, A/B/C evidence grading, a pathway registry JSON, an interactive light-theme atlas HTML, or a Chinese evidence report. Runs multi-database search (PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, bioRxiv + manual WoS/Scopus/Embase/CNKI exports) → dedupe → screening with reasons → grading → registry → atlas → API/Playwright verification → report, as a gated loop with bounded retries and human approval checkpoints. Never fabricates references.
---

# MechAtlas：课题 → 可核查的机制通路图谱

你负责调度；脚本负责检索、计数、构建与核查。**任何文献、数字、PMID/DOI 都只能来自本地保存的 API 记录**。完整规则见 [AGENTS.md](AGENTS.md)。

## 0. 准备
- 读取或创建课题配置：复制 `config/topic-template-v0.1.yaml` → `config/<topic>-v0.1.yaml`，填 PICO、纳入/排除、各库检索式、泳道、分级定义、`contact_email`；重试次数与退避写在 `loop:` 段。示例：`examples/radiation-muscle/`、`examples/ev-intervention/`。
- 先确认联网（Codex 默认关闭网络，见 `prompts/codex-prompt-v0.1.md`）：`python3 scripts/search-pubmed-v0.1.py --query "skeletal muscle" --max 1`。
- 已知关键文献召回测试：列 3–5 篇用户确认的关键文献，检索完成后它们必须在去重集中；不在就改检索式（改检索式需要重新冻结，见下）。

## 1. 用流水线推进，而不是手工串脚本

```bash
python3 scripts/pipeline-v0.1.py run config/<topic>-v0.1.yaml      # 每次都跑这一条；已完成的阶段不会重做
python3 scripts/pipeline-v0.1.py status runs/<topic>
```

`run` 会一直往前走，直到完成或必须停下，退出码说明原因：

| 退出码 | 状态 | 你该做什么 |
|---|---|---|
| 0 | `done` | 发布已获人工批准，交付 |
| 10 | `needs_approval` | 把 `runs/<topic>/pending-approval-v0.1.md` 原样转给用户，**等用户决定**；用户明确回复后才用 `approve` 记录（`--by` 写用户名字）。**绝不自己批准** |
| 11 | `needs_agent_work` | 按 `todo` 完成筛查/提取/重新提取/撤回等工作，然后再 `run` |
| 12 | `waiting_retry` | 用了 `--no-wait`：到点后再 `run` |

状态全部写在 `runs/<topic>/run-state-v0.1.json`（schema：`schema/run-state-v0.1.schema.json`）。

## 2. 阶段、闸门与人工审批点

| 阶段 | 自动做什么 | 闸门（不过不前进） | 有界重试 → 交人工 |
|---|---|---|---|
| (a) 冻结 | 汇总 PICO、纳排标准、每条检索式 | **人工批准**后才检索；配置再改需重新批准 | — |
| 检索 | 各库检索 + 手动导出导入 | 任一库报错或 0 命中即不过；缺失的手动导出记为错误 | 自动重试 3 次，退避约 1/5/15 分钟 → (b) 人工选 `skip` / `change_query` / `switch_source`，**从不静默跳过** |
| 去重 | DOI → PMID → 题名，刷新筛查表（保留判定） | — | — |
| 筛查 | 检查筛查表完整性 | 每条有判定，排除有理由，纳入有等级，A 级非仅摘要 | Agent 工作 |
| 外层循环（最多 2 轮） | | | |
| ├ 提取 | schema + 证据契约；覆盖统计 | 每篇纳入研究进入 ≥1 条边，或在 `registry.unmapped[]` 写**科学**理由（布局不是理由）；进入边的比例 ≥ 阈值 | Agent 补提取最多 2 轮 → (b) |
| ├ (c) 改动 | 与上次批准的基线逐条比对 | 每个等级变更、删边、节点合并/删除都要**人工逐条批准** | 被驳回的条目须撤回 |
| ├ 渲染 | 构建 HTML；图谱数据与登记表**逐条**比对；静态几何；Playwright 真实点击 | 任何缺边、多边、改端点、改等级、几何冲突都不过 | 自动重排最多 3 次（只改行号，不删边、不合并）→ (b) |
| └ 全文升级 | 仅摘要研究查开放全文（Europe PMC / Unpaywall），给出改级提议 | 取到全文的每篇都要重新提取并写 `review` | 有升级就进入下一轮外层循环复核 |
| 引用核查 | `verify-refs`：PMID/DOI 解析 + 题名比对 | 全部 `OK` | 仅网络错误自动重试 |
| 报告 | PRISMA + 报告草稿；闸门摘要块自动同步进 `report-v0.1.md` | 报告存在 | Agent 改写结论 |
| (d) 发布 | 列出将发布的文件 | **人工批准**；批准只对这一版文件有效 | — |

外层循环到上限后若登记表又被改动，会作为 (b) 交人工决定是否再给一轮。

## 3. 你仍需亲手做的判断
- 筛查：逐条写 `ta_decision`/`ta_reason`/`ft_decision`/`ft_reason`/`grade`/`abstract_only`（`screening-v0.1.csv`）。
- 引文追溯：`scripts/snowball-openalex-v0.1.py --seeds studies.json --known deduped.jsonl [--backend s2]`，新候选并入筛查，一轮新增纳入 <3 篇即停。
- 机制登记：按 `schema/registry-v0.1.schema.json` 写 `runs/<topic>/registry-v0.1.json`。不画的纳入研究写进 `unmapped`（`category`：background / redundant(+see) / out-of-scope / scaffold(+node) / insufficient）。
- 全文重新提取：读 `runs/<topic>/fulltext/`，改 `studies-v0.1.json` 的 `abstract_only`/`grade` 与登记表，在 `fulltext-v0.1.json` 对应条目写 `review.decision`（`upgraded` / `kept_abstract_only` + 理由）。
- 报告：把 `report-draft-v0.1.md` 复制为 `report-v0.1.md`，改写「结论先说」；`<!-- mechatlas:gates:begin/end -->` 之间的闸门摘要不要手改。
- 截图：`shots-atlas-v0.1.mjs` 生成后逐张亲眼看。

单个脚本仍可单独使用（`run-search`、`validate-registry`、`build-atlas`、`verify-atlas`、`verify-refs`、`gates-v0.1.py`、`relayout-v0.1.py`、`fulltext-oa-v0.1.py`），但交付必须以流水线的 `done` 为准。

## 4. 交付
中文报告：**结论先说**（3–5 条，每条标【事实】/【关联】/【推测】）→ 检索与 PRISMA → 分级分布 → 各泳道证据表 → 研究缺口 → 核查结果 → 流程闸门与人工审批 → 局限（未检索或人工跳过的库、仅摘要篇数、未尝试全文的篇数）。只发布标识符与筛查判定，不发布 WoS/Scopus/Embase/CNKI 原始导出。发布（复制到 `docs/`、推送、投稿）只在 (d) 批准之后。
