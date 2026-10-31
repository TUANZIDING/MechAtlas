# 在线冒烟测试记录 · Live API smoke test（2026-11-01，北京时间）

目的：证明各检索脚本能对真实 API 跑通（分页、限流重试、统一记录格式）。每个库只取 3 条，**不是**一次正式检索；完整检索请按课题 YAML 运行。

| 库 | 脚本 | 检索式 | 总命中 | 取回 | 结果 |
|---|---|---|---|---|---|
| PubMed | `search-pubmed-v0.1.py` | `(irradiat*[tiab] OR radiotherap*[tiab]) AND ("extracellular vesicle*"[tiab] OR exosome*[tiab]) AND (muscle[tiab] OR myoblast*[tiab]) NOT review[pt]` | 15 | 3 | ✅ 取回含 PMID 41936013（EV 课题已纳入的 A 级研究） |
| Europe PMC（仅预印本） | `search-europepmc-v0.1.py --preprints-only` | `TITLE_ABS:(exosome OR "extracellular vesicles") AND TITLE_ABS:("skeletal muscle") AND SRC:PPR` | 45 | 3 | ✅ Research Square / bioRxiv 预印本，`is_preprint=true` |
| Semantic Scholar | `search-semanticscholar-v0.1.py` | `("extracellular vesicles" \| exosomes) + (irradiation \| radiation) + (muscle \| myoblast)` | 24 | 3 | ✅ |
| Crossref | `search-crossref-v0.1.py` | `exosomes irradiation skeletal muscle myoblast`（`from-pub-date:2015-01-01`） | 297,657* | 3 | ✅ 能取回；*相关性排序，命中数无意义，前几条相关性差——符合预期，只作补充 |
| bioRxiv | `search-biorxiv-v0.1.py` | 2025-06-01..03 窗口，正则 `muscle\|myoblast` | 606（窗口内） | 3 | ✅ 扫描 300 条后命中 3 条 |
| OpenAlex | `search-openalex-v0.1.py` | `exosomes AND "skeletal muscle"` | — | 0 | ❌ HTTP 429：匿名访问按 IP 计日预算，测试所在共享 IP 当日已用完；脚本按 `Retry-After` 判断后立即失败而不是挂起。**需要 `OPENALEX_API_KEY`** |

一键运行器：`run-search-v0.1.py examples/ev-intervention/topic-v0.1.yaml --max 3 --only pubmed,europepmc` 跑通 14 条检索式（PubMed 10 + Europe PMC 4），0 个错误；取回 42 条 → 去重后 34 条（删除 8 条重复），自动生成筛查表；两个手动导出路径不存在时跳过并提示。

引文追溯：`snowball-openalex-v0.1.py --backend s2`，2 个种子（PMID 40835994、DOI 10.1007/s13770-026-00795-4），每个种子取 10 条参考文献/被引 → 21 条新候选（已排除去重集中的记录）。OpenAlex 后端同样因 429 失败，脚本报告错误并以非零码退出。

引用核查：`verify-refs-v0.1.py` 对演示登记表 9 篇：9/9 `OK`（题名相似度均为 1.0），结果见 [verify-refs-v0.1.json](verify-refs-v0.1.json)。负对照：不存在的 PMID 与 DOI 返回 `NOT_FOUND`，题名不符返回 `TITLE_MISMATCH`。
