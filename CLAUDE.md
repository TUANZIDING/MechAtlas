@AGENTS.md

Claude Code：本仓库的技能入口是根目录 `SKILL.md`，同一份内容镜像在 `.claude/skills/mechatlas/SKILL.md`（Codex 读 `.agents/skills/mechatlas/SKILL.md`）。改动 SKILL.md 后运行 `make skills` 同步镜像，CI 会检查三份一致。

课题一律用 `python3 scripts/pipeline-v0.1.py run …` 推进。退出码 10 表示停在人工审批点：把 `runs/<topic>/pending-approval-v0.1.md` 给用户看，等用户在对话里明确决定后，再用 `approve --by <用户名>` 记录；不要替用户批准，也不要为了过闸删边、合并节点或改等级。
