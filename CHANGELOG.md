# 更新记录 · Changelog

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
