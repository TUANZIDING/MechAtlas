# 更新记录 · Changelog

## v0.2.1 — 2026-11-01

- 检索闸门：请求成功但合计 0 条不再退避重试（原先会白等约 21 分钟），直接交人确认——0 条常是真实的证据空白，也可能是检索式过窄；报错（如 429）仍按 60/300/900 秒重试。新增测试 1 个。

## v0.2.0 — 2026-11-01

闸门循环与人工审批。起因是一次脊髓损伤课题的真实运行暴露的四个问题：OpenAlex 返回 429、0 命中却继续往下走；纳入 137 篇只画出 32 条边、其余去向无记录；IL-7→凋亡（PMID 31068251）一条边没画出来、NeuroD1 与 PLD1 被合并，验证仍然通过；66 篇仅摘要的研究没有尝试找开放获取全文。

- 新增流水线 `scripts/pipeline-v0.1.py`（`run` / `status` / `approve`）：可续跑的状态机，状态写在 `runs/<topic>/run-state-v0.1.json`（schema：`schema/run-state-v0.1.schema.json`），退出码区分完成 0、待审批 10、待 Agent 补活 11、等待重试 12、出错 1。
- 检索闸门：报错或 0 命中按配置退避重试（默认 3 次，约 1/5/15 分钟），用尽后停下来请人选择跳过、改检索式或换数据源，不再静默跳过；`run-search-v0.1.py` 有失败时退出码 3（`--allow-failures` 可放行）。
- 提取覆盖闸门（`scripts/gates-v0.1.py coverage`）：每篇纳入研究要么映射到至少一条边，要么在登记 `unmapped[]` 里写明类别与理由（背景 / 冗余 / 超范围 / 骨架 / 证据不足）；「布局放不下」不算理由。低于阈值最多重提取 2 轮，再上报。
- 渲染一致性闸门：从图谱 HTML 里取回嵌入数据，与登记逐条对照（节点、边 id、端点、来源卡），再做静态几何检查（节点重叠、连线穿节点、越出泳道）；静态通过后才跑浏览器真实点击。不一致时 `scripts/relayout-v0.1.py` 只调整行位置重排，最多 3 次，再上报；绝不删边、并节点。
- 全文升级：`scripts/fulltext-oa-v0.1.py` 为仅摘要研究查 Europe PMC 全文 XML 与 Unpaywall，取到后由 Agent 复核、提出分级变更。
- 外层循环上限 2 轮；最后一轮不再抓全文，记为 `not_attempted_cap`。
- 四个人工审批点：检索策略与纳排标准冻结、任一闸门重试用尽、分级变更 / 删边 / 并节点（逐项列出、逐项批准）、最终发布。审批与输入指纹绑定，输入变了就要重新批准。
- 登记 schema：节点 `mergedFrom`、顶层 `unmapped`。图谱模板给每组连线加 `data-ids`；`verify-atlas-v0.1.mjs` 增加一对一核对（每条关系在自己的端点间恰好画一次、路径有效、节点 id 与登记一致），单次点击失败记为不通过而不是崩溃，共 32 项检查。
- 中文报告新增「闸门与审批记录」一节（由运行状态生成，确定性），并在局限中写明跳过的数据库与未尝试的全文。
- 测试增至 51 项（新增 29 项，覆盖退避、上报、续跑、审批、重排、全文、发布绑定）。

## v0.1.0 — 2026-11-01

首个公开版本，由 [radiation-muscle-pathway](https://github.com/TUANZIDING/radiation-muscle-pathway)（v0.7 分子通路图谱、EV 干预通路图 v0.2）的做法提炼而来。

- 技能入口 `SKILL.md`（镜像到 `.agents/skills/`、`.claude/skills/`）与总规则 `AGENTS.md`。
- 课题配置模板 `config/topic-template-v0.1.yaml`，以及两个真实课题示例（检索式逐字取自原项目）。
- 检索脚本：PubMed（E-utilities 历史服务器分页）、Europe PMC（含预印本）、OpenAlex、Crossref、Semantic Scholar、bioRxiv/medRxiv；引文追溯（OpenAlex 或 Semantic Scholar）；去重；PRISMA 计数与 SVG；引用核查；一键运行器。
- 手动导出导入：Web of Science（纯文本 / 制表符）、Scopus（CSV / RIS）、Embase（RIS）、CNKI/万方（RefWorks / EndNote / NoteExpress）；可选 WoS Starter API、Elsevier Scopus API（需自备 key）。
- JSON Schema：记录、纳入研究、机制登记。
- 通用浅色交互图谱模板 + 构建脚本（证据契约）+ Playwright 真实点击验证 + 截图脚本。
- 演示：从 EV 干预通路图 v0.2 节选 9 篇真实文献、13 条关系，发布为 GitHub Pages。
- 22 项离线单元测试（合成样例，非真实导出）；CI：单元测试 + 构建 + Chromium 验证。
