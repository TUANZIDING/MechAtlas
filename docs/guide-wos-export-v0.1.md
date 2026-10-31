# 导入 Web of Science / CNKI 手动导出（v0.1）

**为什么要手动：** 苏州大学的 Web of Science 通过 WebVPN 给人用，Agent 不能替你登录，也不应抓取。你导出文件，Agent 负责规范化、去重、筛查。原始导出受 Clarivate / CNKI 条款约束，**只放在本地 `inputs/`，不要提交、不要公开**。

## Web of Science（核心合集）

1. 经学校图书馆 → WebVPN 进入 Web of Science，选择 **Web of Science Core Collection**。
2. 用 **Advanced Search** 粘贴课题 YAML 里 `manual_exports` 的 `query`（`TS=(...)` 语法），记下检索日期和总命中数。
3. 结果页点 **Export** → **Plain text file**（推荐）或 **Tab delimited file**。
   - Record Content 选 **Full Record**（含摘要）；不需要 Cited References。
   - 每次导出有条数上限（以界面提示为准，常见为 1,000 条），命中多时分批：1–1000、1001–2000……
4. 把文件放到 YAML 写的路径，例如 `inputs/<课题>/wos/savedrecs-1.txt`、`savedrecs-2.txt`。
5. 运行：

```bash
python3 scripts/import-export-v0.1.py --format wos --db wos inputs/<课题>/wos/*.txt \
   --query 'TS=(...)' --out runs/<课题>/raw/wos-wos1.jsonl
# 或者直接重跑整套检索：run-search 会按 YAML 的 manual_exports 自动导入
python3 scripts/run-search-v0.1.py config/<课题>-v0.1.yaml --skip-exports   # 不导入
python3 scripts/run-search-v0.1.py config/<课题>-v0.1.yaml                  # 导入
```

制表符文件常是 UTF-16 编码，脚本会自动识别。导入后的记录带 WoS 的 `UT` 号（`raw_id`）、DOI、PMID（如有），去重时会和 PubMed 等合并。

## CNKI 知网 / 万方 / 维普

1. 检索（建议"专业检索"，把检索式写进 YAML 的 `query`），勾选结果。
2. **导出与分析 → 导出文献**，选 **RefWorks**、**EndNote** 或 **NoteExpress** 任一格式，保存为 `.txt`。每次可导出的条数以界面上限为准，多了分批。
3. 放到 `inputs/<课题>/cnki/`，运行：

```bash
python3 scripts/import-export-v0.1.py --format cnki --db cnki inputs/<课题>/cnki/*.txt --out runs/<课题>/raw/cnki-cnki1.jsonl
```

格式自动识别（RefWorks / EndNote / NoteExpress / RIS），编码支持 UTF-8 和 GB18030。中文题名去重时按字计权，CNKI 与万方的同一篇文章能合并。

## Scopus / Embase（学校未订阅时可跳过）

- Scopus：Export → CSV（勾选 Abstract、PubMed ID、DOI）或 RIS → `--format scopus-csv` / `--format ris --db scopus`。
- Embase：Export → RIS → `--format ris --db embase`（PMID 从 `C5` 字段或含 "PMID" 的字段识别）。

## 发布时只给什么

- 可以发布：PMID、DOI、WoS UT、Scopus EID、题名（期刊公开信息）、你的筛查判定与理由、分级。
- 不要发布：导出文件本身、成批摘要文本、WoS/Scopus 的引文计数等受许可数据。
