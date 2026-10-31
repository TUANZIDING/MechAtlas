# 数据库覆盖与限制 · Database coverage（v0.1，2026-11-01 核对）

**结论：** 开放 API 能覆盖 PubMed/MEDLINE、PMC、预印本和大部分带 DOI 的期刊；Web of Science、Scopus、Embase、中文库只能由你在机构网页里手动导出后导入。没导入的库在报告里一律写"未检索"。

## 开放数据库（脚本直接检索）

| 库 | 脚本 | 覆盖 | 检索语法 | 限制与注意 |
|---|---|---|---|---|
| PubMed | `search-pubmed-v0.1.py` | MEDLINE + PubMed 全部 | PubMed 语法（`[tiab]`、`[Mesh]`、`[dp]`） | 无 key 3 次/秒，`NCBI_API_KEY` 10 次/秒；用 history server 分页，单次检索上限 10,000 条 |
| Europe PMC | `search-europepmc-v0.1.py` | PubMed + PMC 全文 + 预印本（`SRC:PPR`：bioRxiv、medRxiv、Research Square 等）+ Agricola | `TITLE_ABS:`、`SRC:`、`PUB_YEAR:[a TO b]` | 不加 `TITLE_ABS` 时会检全文，命中数会很大 |
| OpenAlex | `search-openalex-v0.1.py` | 全学科、带 DOI 的期刊/会议/书/预印本 | 布尔表达式（题名+摘要） | **2026 起匿名访问按 IP 计日预算，共享网络很快用完（HTTP 429）；请设 `OPENALEX_API_KEY`（免费）** |
| Semantic Scholar | `search-semanticscholar-v0.1.py` | 全学科，含 arXiv、会议 | bulk 语法：`+` 与、`|` 或、`-` 非 | 匿名共享池常 429，脚本退避重试；`S2_API_KEY` 更稳 |
| Crossref | `search-crossref-v0.1.py` | 所有注册 DOI（期刊、书、会议、posted-content） | 自由文本，按相关性排序 | **命中数没有意义**，只取前 N 条；摘要常缺 |
| bioRxiv / medRxiv | `search-biorxiv-v0.1.py` | 两个预印本服务器 | **无关键词检索**：按日期窗口扫描 + 本地正则 | 慢且限流；报告必须写扫描比例（EV 课题 2024–2026 只扫了约 30%） |

引文追溯：`snowball-openalex-v0.1.py`（OpenAlex，需 key）或 `--backend s2`（Semantic Scholar references / citations）。

## 订阅数据库（手动导出 → 导入）

| 库 | 苏州大学（2026-10） | 导出格式 | 导入命令 |
|---|---|---|---|
| Web of Science 核心合集 | 有，经 WebVPN | 纯文本（全记录）或制表符分隔 | `import-export-v0.1.py --format wos --db wos` |
| Scopus | **未订阅** | CSV 或 RIS | `--format scopus-csv` / `--format ris --db scopus` |
| Embase | **未订阅** | RIS | `--format ris --db embase` |
| CNKI 知网 | 有 | RefWorks / EndNote / NoteExpress | `--format cnki --db cnki` |
| 万方 | 视学校订阅 | NoteExpress / RefWorks / EndNote | `--format cnki --db wanfang` |
| 维普 | 视学校订阅 | RefWorks / EndNote | `--format cnki --db vip` |

为什么 Agent 不能直接检索这些库：

- 机构订阅是给人用的网页（IP 或 WebVPN 认证），没有给 Agent 的 API；自动化抓取违反 Clarivate、Elsevier、CNKI 的使用条款，也可能导致学校账号被封。
- 原始导出受许可约束：**不要提交到 Git，不要放到网页上**。`.gitignore` 已排除 `inputs/` 和 `runs/`。仓库里只发布标识符（PMID、DOI、WoS UT、Scopus EID）和筛查判定。
- 如果你或学校有 API key：`importers/wos-starter-api-v0.1.py`（`WOS_STARTER_KEY`，仅元数据无摘要）、`importers/elsevier-api-v0.1.py`（`ELSEVIER_API_KEY`，常需机构 IP 或 `ELSEVIER_INSTTOKEN`）。两者 v0.1 未做真实 key 测试。

## 选库建议

- 基础医学机制课题：PubMed + Europe PMC（含预印本）是底线；再加 Semantic Scholar 或 OpenAlex 补非 MEDLINE 期刊；有 WoS 就补 WoS（v0.2 EV 图谱靠 WoS 补到 3 篇）。
- 中文文献：只有 CNKI/万方能检到中文期刊；不检就在报告里写明"中文库未检索"，不要说"全球无研究"。
- 每个库都记录：检索日期、检索式原文、总命中数、取回数。`run-search-v0.1.py` 会自动写入 `search-log-v0.1.json`；手动导出请在 YAML 的 `manual_exports` 里填 `query` 和 `date`。
