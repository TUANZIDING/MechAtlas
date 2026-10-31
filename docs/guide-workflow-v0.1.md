# 工作流程 · Workflow（v0.1）

**一句话：** 先把课题写成一份 YAML，脚本负责检索、去重、计数、构建和核查；人（或 Agent）只做三件需要判断的事——筛查、分级、画机制关系。

```
topic YAML ─► run-search ─► raw/*.jsonl ─► dedupe ─► screening.csv ─► (人工/Agent 判定)
                 ▲                                         │
   手动导出 inputs/ (WoS/Scopus/Embase/CNKI)               ▼
                                         snowball ◄── studies.json (A/B/C)
                                                         │
                                                         ▼
                              registry.json ─► build-atlas ─► atlas.html
                                   │                           │
                              validate-registry          verify-atlas (Playwright 真实点击)
                                   │                           │
                              verify-refs (API) ───────► report.md + PRISMA
```

## S0 课题配置

1. 复制 `config/topic-template-v0.1.yaml` 为 `config/<课题>-v0.1.yaml`。
2. 写清 PICO 和纳入/排除标准。**排除标准里要列出常见的"假阳性"**（例如辐射课题要排除激光、超声、微波等非电离辐射）。
3. 检索式分两层：一条**基础检索**（暴露 × 目标组织），再加若干**模块检索**（基础检索 AND 某条通路的关键词）。基础检索命中太多（如 > 2,000）时，按设计不逐条筛，改为模块检索 + 近年全量 + 补充检索，并在报告里写明残余缺口——辐射肌损伤 v0.7 就是这样做的。
4. 冒烟测试：`python3 scripts/run-search-v0.1.py config/<课题>-v0.1.yaml --max 3`，看每条检索式有没有命中、有没有 error。
5. **已知文献召回测试：** 请用户给 3–5 篇他确认相关的文献，检索完成后在 `deduped-v0.1.jsonl` 里搜它们的 PMID/DOI。漏了就改检索式（v0.7 的经验：只用模块检索漏掉了 c-Abl、NO、Ca²⁺、CD47 等直接照射研究，是补充检索才找回来的）。

## S1–S2 检索与去重

```bash
export MECHATLAS_EMAIL=you@example.org        # API 礼貌访问
python3 scripts/run-search-v0.1.py config/<课题>-v0.1.yaml
```

- 每条检索式写一个 `raw/<库>-<id>.jsonl`，所有记录统一为 `schema/record-v0.1.schema.json` 的格式。
- 某个库失败（限流、需要 key）不会中断整次运行，会在 `search-log-v0.1.json` 里记 `status: error`。报告里必须写明。
- 去重顺序：DOI → PMID → 规范化题名（中文题名按字计权）。合并后的记录保留每次命中（`hits`），可以统计"只在某库出现"的记录数。

## S3 筛查

打开 `runs/<课题>/screening-v0.1.csv`（带 BOM，Excel/WPS 直接打开）：

| 列 | 怎么填 |
|---|---|
| `ta_decision` | 题名/摘要阶段：`include` / `exclude` / `maybe` |
| `ta_reason` | 排除理由（必须写，如"非电离辐射""综述""无骨骼肌读出"） |
| `ft_decision` | 全文阶段：`include` / `exclude` / `not_retrieved` |
| `ft_reason` | 全文排除理由，或纳入要点 |
| `abstract_only` | 只读了摘要填 `yes` |
| `grade` | `A` / `B` / `C` |
| `lanes` | 所属泳道 id，分号分隔 |
| `triage` | 脚本给的提示（主题词缺失、综述），**只是提示，不代替判断** |

全文来源：Europe PMC `fullTextXML`、PMC OA、出版社开放获取页面。取不到全文就标 `not_retrieved`，或作为仅摘要纳入（最高 B 级）。

## S4 引文追溯

```bash
python3 scripts/screen-sheet-v0.1.py include runs/<课题>/screening-v0.1.csv --out runs/<课题>/studies-v0.1.json
python3 scripts/snowball-openalex-v0.1.py --seeds runs/<课题>/studies-v0.1.json \
   --known runs/<课题>/deduped-v0.1.jsonl --out runs/<课题>/snowball-r1.jsonl      # 无 OpenAlex key 加 --backend s2
```

把新候选并入筛查（`dedupe` 后 `screen-sheet make`），判定后再跑下一轮；**一轮新增纳入少于 3 篇就停止**。

## S5–S6 分级与机制登记

- 分级规则见 [guide-grading-v0.1.md](guide-grading-v0.1.md)。
- 机制登记 `registry-v0.1.json` 按 `schema/registry-v0.1.schema.json` 写：
  - `lanes`：机制泳道（`r0`/`r1` 行范围，`color` 主色）；`cols`：从左到右的因果层级。
  - `nodes`：`c` 列号、`r` 行号；`status: evidence | scaffold`。
  - `edges`：`direction`（+ 激活 / − 抑制）、`grade`、`causal`、`refs`（来源 id）、`model`、`dose`、`time`、`method`、`functional`、`limitation`。
  - `sources`：每张来源卡 = 一篇纳入研究（PMID 或 DOI），`abstractOnly` 标记。
- 布局经验：一条机制链占一行；同一泳道里一行最多 6 个节点；单行泳道的标签要短（验证脚本会检查标签是否超出泳道高度）。

## S7–S8 构建与核查

```bash
python3 scripts/validate-registry-v0.1.py --registry R.json --studies S.json
node scripts/build-atlas-v0.1.mjs --registry R.json --studies S.json --out docs/<课题>.html
python3 scripts/verify-refs-v0.1.py R.json --out docs/<课题>/verify-refs-v0.1.json
CHROMIUM_PATH=/usr/bin/chromium node scripts/verify-atlas-v0.1.mjs --html docs/<课题>.html --registry R.json --studies S.json --out docs/<课题>/validation-v0.1.json
CHROMIUM_PATH=/usr/bin/chromium node scripts/shots-atlas-v0.1.mjs --html docs/<课题>.html --outdir docs/<课题>/ --node <节点id> --edge "<源>><靶>"
```

验证脚本会**逐个真实点击所有节点**，检查检查器里出现对应 PMID/DOI；每个等级点一条边；A/B/C 筛选；中英切换；控制台错误必须为空；节点不重叠、连线不穿过其他节点、编号徽章不压节点、标题不溢出、泳道标签不溢出；1440 px 与 390 px 都无横向溢出。截图生成后**逐张亲眼看**。

## S9 报告

```bash
python3 scripts/prisma-v0.1.py --search-log … --dedupe-stats … --sheet … --snowball … --out-md … --out-svg … --out-json …
python3 scripts/report-v0.1.py --topic … --registry … --studies … --prisma … --verify … --validation … --out report-v0.1.md
```

`report-v0.1.py` 只生成由数据计算的骨架；Agent 再改写「结论先说」，每条标【事实】/【关联】/【推测】，不得引入文件里没有的数字或文献。
