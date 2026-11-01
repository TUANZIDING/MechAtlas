# AGENTS.md

本仓库是 **MechAtlas**：给 Codex / Claude Code 等 Agent 用的技能与工具箱，把一个生物医学课题变成
「高召回系统检索 → 去重与 PRISMA 计数 → 带理由的筛查 → A/B/C 证据分级 → 机制/信号通路登记 JSON →
由 JSON 渲染的单文件浅色交互图谱 → API 与真实点击核查 → 中文报告」。

先读本文件，再按阶段读 [SKILL.md](SKILL.md) 和 `docs/` 里需要的那一篇。保持本文件简短。

## 底线（永远成立）

1. **绝不编造文献。** 每一条纳入记录都必须是**本地保存的、由 API 取回的记录**（`runs/<topic>/raw/*.jsonl` 或手动导出经 `import-export-v0.1.py` 规范化后的 JSONL）。不能凭记忆写 PMID、DOI、题名、年份、期刊或结果数字。查不到就写「未找到」，不要用"看起来合理"的文献填空。
2. **每个 PMID/DOI 都要解析核对**：`scripts/verify-refs-v0.1.py` 状态必须全部 `OK`（PubMed esummary / Crossref / doi.org，题名相似度 ≥ 0.85）。
3. **每条证据边都要有已纳入的来源**：边的 `refs` → 来源卡 → 纳入研究（PMID/DOI 对得上）。构建脚本与 `validate-registry-v0.1.py` 会强制检查。
4. **标注阅读深度**：只读了摘要的纳入研究 `abstract_only: true`，图中来源卡显示「仅摘要」；**A 级必须读过全文**。
5. **区分事实 / 关联 / 推测**：报告每条结论标【事实】（原文直接测得）、【关联】（相关或单项比较，未做因果检验）、【推测】（推断、生信预测、跨模型外推）。边上用 `causal: true` 只表示做过因果检验（敲除、抑制剂、挽救、antagomiR 等）。
6. **方向矛盾或只有假说的机制**画成骨架节点（`status: scaffold`），**不连证据边**，也不下因果结论。
7. **图谱只能由数据渲染**（`templates/` + `build-atlas-v0.1.mjs`），不得用图像生成模型画通路图；页面上的统计数字由数据计算，不手写。
8. **中文报告结论先行**：先给 3–5 条结论，再给检索、PRISMA、证据表、缺口、核查与局限；写明未检索的数据库及原因。
9. **订阅数据库的原始导出不得公开**（Clarivate / Elsevier / CNKI 条款）：放 `inputs/`（已 gitignore），仓库与网页只发布标识符（PMID/DOI/UT/EID）和筛查判定。
10. Agent **不能登录机构网页订阅**（如苏州大学 WebVPN 的 Web of Science）：请用户手动导出，按 [docs/guide-wos-export-v0.1.md](docs/guide-wos-export-v0.1.md) 导入。只有用户提供 API key（环境变量）时才用 `importers/*-api-v0.1.py`。
11. 不覆盖用户的手工判定；`screen-sheet-v0.1.py make` 刷新时保留已有判定。对外发送、投稿、公开发布需用户明确同意。
12. **用 `scripts/pipeline-v0.1.py run` 推进课题**。它停在人工审批点（退出码 10）时，把 `pending-approval-v0.1.md` 交给用户，等用户明确决定后才运行 `approve --by <用户名>`；**Agent 绝不自行批准**，也不绕过闸门（不删边、不合并节点、不写「布局」理由来过闸）。
13. 四个人工审批点：(a) 冻结检索策略与纳排标准；(b) 任何闸门重试用尽；(c) 每个证据等级变更、删边、节点合并（逐条）；(d) 发布前。其余（去重、引用核查、schema 检查、重排）自动运行。

## 默认分级（可在课题 YAML 的 `grades:` 中改写定义）

| 等级 | 默认定义 | 例（辐射肌损伤课题） |
|---|---|---|
| A | 目标课题的直接模型 | 照射骨骼肌/肌源细胞中直接测得 |
| B | 邻近/非目标模型，或条件性、仅摘要级 | 其他损伤模型；剂量/时相依赖；会议摘要 |
| C | 推断、候选、间接 | 生信预测、肿瘤外泌体介导、仅 mRNA 推断 |
| 骨架 | 方向矛盾或只有假说 | Myostatin mRNA↓/蛋白↑；cGAS–STING 0 篇 |

细则见 [docs/guide-grading-v0.1.md](docs/guide-grading-v0.1.md)。

## 流程（一个课题一个运行目录 `runs/<topic>/`）

反复运行 `python3 scripts/pipeline-v0.1.py run config/<topic>-v0.1.yaml`，按停下的原因做事（详见 [SKILL.md](SKILL.md) 第 1–2 节）。重试次数、退避、阈值都在课题 YAML 的 `loop:` 段。

| 阶段 | 谁做 | 闸门 | 有界重试 → 人工 |
|---|---|---|---|
| S0 配置 + (a) 冻结 | Agent 写 YAML，人批准 | 批准前不检索；改配置需重新批准 | — |
| S1–S2 检索与去重 | 自动 | 任一库报错或 0 命中即不过 | 报错重试 3 次（约 1/5/15 分钟）；0 命中不重试 → (b)：skip / change_query / switch_source |
| S3 筛查 | Agent | 每条有判定、排除有理由、纳入有等级 | — |
| S4 引文追溯 | Agent（`snowball-openalex-v0.1.py`） | 一轮新增纳入 <3 篇即停 | — |
| S5–S6 提取与登记 | Agent 写 `registry-v0.1.json`；自动查 schema/契约/覆盖 | 每篇纳入研究进入 ≥1 条边或有 `unmapped` 科学理由；比例 ≥ 阈值 | 补提取 2 轮 → (b) |
| (c) 改动审批 | 人逐条 | 等级变更、删边、节点合并/删除 | 驳回项由 Agent 撤回 |
| S7 构建 + 渲染一致性 | 自动 | 图谱与登记表逐条一致，几何无冲突，真实点击全过 | 自动重排 3 次 → (b) |
| 全文升级 | 自动取 OA 全文，Agent 重新提取 | 取到的全文都已复核 | 有升级则进入第 2 轮外层循环（上限 2 轮） |
| S8 引用核查 | 自动 | `verify-refs` 全部 OK | 仅网络错误重试 |
| S9 报告 + (d) 发布 | Agent 改写结论，人批准发布 | 闸门摘要自动同步；批准绑定具体文件 | — |

## 约定

- 新文件名不用下划线并带版本号（如 `search-pubmed-v0.1.py`）；README.md、LICENSE、AGENTS.md、SKILL.md、CHANGELOG.md、CITATION.cff 例外。
- Python 只用标准库（YAML 需 PyYAML）；Node 只用 Playwright。
- 页面一律浅色主题；简体中文为主，英文为辅。
- API 礼貌访问：在 YAML 填 `contact_email` 或设 `MECHATLAS_EMAIL`；可选 `NCBI_API_KEY`、`OPENALEX_API_KEY`、`S2_API_KEY`。

## 文档路由

| 需要 | 读 |
|---|---|
| 一步步怎么做、闸门与审批点 | [docs/guide-workflow-v0.1.md](docs/guide-workflow-v0.1.md) |
| 各数据库能做什么、限制 | [docs/guide-databases-v0.1.md](docs/guide-databases-v0.1.md) |
| 导入 Web of Science / CNKI 手动导出 | [docs/guide-wos-export-v0.1.md](docs/guide-wos-export-v0.1.md) |
| 分级与骨架节点 | [docs/guide-grading-v0.1.md](docs/guide-grading-v0.1.md) |
| 现成的 Codex 提示词 | [prompts/codex-prompt-v0.1.md](prompts/codex-prompt-v0.1.md) |
