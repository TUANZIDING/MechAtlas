# 工作流程 · Workflow（v0.1）

**一句话：** 先把课题写成一份 YAML，脚本负责检索、去重、计数、构建和核查；人（或 Agent）只做三件需要判断的事——筛查、分级、画机制关系。

下图是数据流；实际推进用 `pipeline-v0.1.py`（见下一节），它在图中各步之间加了闸门和人工审批点。

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

## 用流水线推进：闸门、有界重试与人工审批

从 S0 写好配置开始，整个课题都用一条命令推进，反复运行即可：

```bash
python3 scripts/pipeline-v0.1.py run config/<课题>-v0.1.yaml      # 加 --no-wait：遇到重试退避不阻塞，到点再跑
python3 scripts/pipeline-v0.1.py status runs/<课题>
```

它会自己往前走，走不动就停下并说明原因：退出码 **10** 表示要人拍板，**11** 表示要 Agent 干活（筛查、写登记表、补提取、读全文），**12** 表示在等重试时间，**0** 表示发布已批准。每一步的结果都记在 `runs/<课题>/run-state-v0.1.json`，报告里的「流程闸门与人工审批」一节就是从这里生成的。

**哪些事一定要人来定（流水线会停下等）：**

| 审批点 | 什么时候 | 你要决定什么 |
|---|---|---|
| (a) 冻结 | 检索之前；之后只要改了检索式、纳排标准或换库，就要重新批准 | 检索策略和纳入/排除标准是否定稿 |
| (b) 重试用尽 | 任何闸门把自动重试用完 | 检索：跳过该库 / 改检索式 / 换数据源；覆盖或渲染：接受现状（写进报告）或再给一组重试 |
| (c) 证据改动 | 与上次批准的版本相比，出现等级变更、删边、节点合并或删除 | 逐条批准或驳回；驳回的由 Agent 撤回 |
| (d) 发布 | 一切核查通过、报告写好之后 | 这一版文件能否对外发布；文件再改就要重新批准 |

待批事项写在 `runs/<课题>/pending-approval-v0.1.md`，里面附有记录决定的命令，例如：

```bash
python3 scripts/pipeline-v0.1.py approve runs/<课题> --checkpoint gate-retrieval-1 --item openalex=skip --by 张三 --note "无 OpenAlex key"
python3 scripts/pipeline-v0.1.py approve runs/<课题> --checkpoint changes-1 --decision approve --item delete:edge:E7=reject --by 张三
```

**闸门与重试（次数都可在 YAML 的 `loop:` 段改）：**

- **检索闸门**：某个库报错（如 OpenAlex 429），就只重跑失败的检索式，等待时间逐次拉长（默认 60、300、900 秒），最多 3 次；还不行就交给人决定，绝不悄悄跳过。请求成功但全部检索式合计 0 条，不重试、直接交人确认（可能是真实的证据空白，也可能检索式过窄）。手动导出文件找不到也算失败（重试没有意义，直接交人）。
- **提取覆盖闸门**：每篇纳入研究要么至少进入一条证据边，要么在登记表 `unmapped` 里写清为什么不画——只接受 background、redundant（指明同结论的来源卡）、out-of-scope、scaffold（指明骨架节点）、insufficient 这几类科学理由，**"布局放不下"不算理由**。另外，进入证据边的研究比例要达到阈值（默认 50%）。不达标就让 Agent 补提取，最多 2 轮，之后交人。
- **渲染一致性闸门**：图谱构建后，把页面里的数据和登记表逐条对：每条关系恰好画一次、端点与方向与等级一致、节点不多不少；再做静态几何检查和 Playwright 真实点击。不一致或布局冲突就自动重排（只挪节点所在的行，必要时把泳道加高一行；不删边、不合并节点），最多 3 次，之后交人。
- **全文升级**：对仅读摘要的纳入研究，自动去 Europe PMC 和 Unpaywall 找开放获取全文，存到 `runs/<课题>/fulltext/`，并给出改级建议。Agent 读完全文重新提取后，等级变化会进入审批点 (c)，然后开始第 2 轮外层循环重新核查。外层循环最多 2 轮。
- 去重、schema 与证据契约检查、引用核查都自动进行，不需要人。

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
- 某个库失败（限流、需要 key、0 命中、手动导出缺失）不会中断整次运行，会在 `search-log-v0.1.json` 里记下来，但命令以退出码 3 结束并列出失败项。单独使用时请自己决定怎么补；用流水线时由检索闸门自动重试，再交人决定。
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
  - `unmapped`：纳入了但不画边的研究，每条写 `pmid`/`doi`、`category` 和 `reason`（见上文「提取覆盖闸门」）。
  - 合并节点时在保留的节点上写 `mergedFrom: [旧 id]`；合并、删边、改等级都会进入人工审批。
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

`report-v0.1.py` 只生成由数据计算的骨架；Agent 再改写「结论先说」，每条标【事实】/【关联】/【推测】，不得引入文件里没有的数字或文献。加 `--run-state runs/<课题>/run-state-v0.1.json` 会附上「流程闸门与人工审批」一节；用流水线时它在 `report-v0.1.md` 的 `<!-- mechatlas:gates:begin -->`…`end` 之间自动同步，不要手改这一段。
