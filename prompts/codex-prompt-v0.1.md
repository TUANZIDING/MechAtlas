# Codex 提示词 v0.1（可直接粘贴）

## 先打开网络

MechAtlas 要访问 PubMed、Europe PMC 等 API，**Codex 默认不联网**：

- **Codex CLI / IDE 插件 / 桌面 App**：默认 `workspace-write` 沙箱关闭网络。任选其一：
  - 在 `~/.codex/config.toml` 加入：
    ```toml
    [sandbox_workspace_write]
    network_access = true
    ```
  - 或单次启动：`codex -c 'sandbox_workspace_write.network_access=true'`
  - 官方说明：<https://developers.openai.com/codex/agent-approvals-security>（"Network access" 一节）。有用户反映桌面 App 对该配置的支持与 CLI 不完全一致，联网失败时改用 CLI。
- **Codex Cloud**：智能体阶段默认禁止联网（setup 脚本可以联网装依赖）。在环境设置里为该环境打开 **Agent internet access**，建议用域名白名单，只放：
  `eutils.ncbi.nlm.nih.gov, pubmed.ncbi.nlm.nih.gov, www.ebi.ac.uk, api.openalex.org, api.crossref.org, api.semanticscholar.org, api.biorxiv.org, doi.org, www.ncbi.nlm.nih.gov`，HTTP 方法只需 GET/HEAD。说明：<https://developers.openai.com/codex/cloud/internet-access>。
- **Claude Code**：Bash 里的 `curl`/`python` 联网前会请求授权；同意即可。

联网自检：`python3 scripts/search-pubmed-v0.1.py --query "skeletal muscle" --max 1`，应返回一行 JSON。

---

## 提示词（把【】换成你的课题）

```text
你在 MechAtlas 仓库里工作（https://github.com/TUANZIDING/MechAtlas）。先读 AGENTS.md 和 SKILL.md，严格按其规则执行。

课题：【例：电离辐射通过哪些分子机制与信号通路导致骨骼肌损伤、修复障碍与纤维化？】
目标组织/模型：【例：骨骼肌、肌卫星细胞/成肌细胞（含 C2C12）、FAP、肌内微血管】
暴露/干预：【例：X 线、γ 射线、质子、重离子】
分级口径：A=【直接模型】；B=【邻近模型/条件性/仅摘要】；C=【推断/候选】；方向矛盾或只有假说画骨架节点不连边。
联系邮箱（API 礼貌访问）：【you@example.org】
我会手动提供的订阅库导出：【例：苏州大学 WebVPN 的 Web of Science 纯文本导出，放在 inputs/<topic>/wos/；Scopus/Embase 未订阅；中文库：检/不检】
已知关键文献（召回测试用）：【PMID/DOI 3–5 篇】

请用流水线推进：反复运行 python3 scripts/pipeline-v0.1.py run config/<topic>-v0.1.yaml --run-dir runs/<topic>，按它的退出码行事——0 完成；10 停下来把 runs/<topic>/pending-approval-v0.1.md 原样转给我，等我批准（你绝不能自己运行 approve）；11 按提示补完你的活再重跑；12 在等检索重试，稍后再跑。以下各步都在流水线的闸门之内，产出写进 runs/<topic>/，不要跳步：
1. 复制 config/topic-template-v0.1.yaml 为 config/<topic>-v0.1.yaml，填 PICO、纳入/排除、各库检索式（基础检索 + 每条通路一个模块检索 + 近年全量 + 补充检索）、泳道、分级定义。先用 --max 3 冒烟，报告每条检索式命中数。
2. 检索策略和纳排标准冻结前，流水线会停下来请我批准。之后它跑全量检索 + 导入我的手动导出；某个库报错会自动退避重试，用尽后停下来请我决定跳过、改检索式还是换数据源；请求成功但 0 命中则直接停下来请我确认，不要自己跳过。确认已知关键文献都在去重集里。
3. 在 screening-v0.1.csv 逐条做题名/摘要筛查，每条排除写理由；对通过者取全文（Europe PMC fullTextXML / PMC OA），做全文筛查；拿不到全文的标 abstract_only=yes（最高 B 级）。A 级必须读全文。
4. 用 snowball-openalex-v0.1.py 对纳入集做引文追溯（没有 OPENALEX_API_KEY 就加 --backend s2），新候选并入筛查，直到一轮新增纳入 < 3 篇。
5. 导出 studies-v0.1.json；按 schema/registry-v0.1.schema.json 写 registry-v0.1.json：节点放在泳道和因果列里，每条边写 direction / grade / causal / refs / model / dose / time / method / functional / limitation。没画成边的纳入研究写进 unmapped[]，给出类别（background / redundant / out-of-scope / scaffold / insufficient）和理由；「布局放不下」不是理由。删边、并节点、改分级都会逐项列给我批准。
6. validate-registry-v0.1.py 必须 ok；流水线用 build-atlas-v0.1.mjs 构建图谱（浅色主题，由数据渲染，禁止用图像模型画图），并逐条对照登记检查每个节点和每条边都画出来了；不一致时只重排位置，不许删边或并节点。之后对仅摘要的研究找开放获取全文，取到的由你复核并提出分级变更。
7. 核查：verify-refs-v0.1.py 全部 OK；verify-atlas-v0.1.mjs 全部 PASS（真实点击、控制台错误为空、无重叠、390px 无横向溢出）；shots-atlas-v0.1.mjs 截图（DPR 2），每张截图你自己打开看一遍再用。
8. 用 prisma-v0.1.py 和 report-v0.1.py 生成 PRISMA 与报告骨架，然后写中文报告：结论先说（3–5 条，每条标【事实】/【关联】/【推测】）→ 检索与 PRISMA → 分级分布 → 各泳道证据表 → 研究缺口 → 核查结果 → 局限（写明未检索的库、仅摘要篇数、未逐条筛的残余记录）。

硬性规则：绝不编造文献、PMID、DOI 或数字；所有纳入记录必须是本地保存的 API 记录；不提交 inputs/ 下的原始导出，只发布标识符和判定；文件名不用下划线并带版本号（如 xxx-v0.1.json）。发布前流水线会最后停一次请我批准。完成后用中文简要汇报：闸门与审批记录、各库命中/取回、去重后数量、纳入数（A/B/C）、图谱路径、核查结果、局限。
```
