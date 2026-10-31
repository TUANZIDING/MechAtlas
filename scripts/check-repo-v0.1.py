#!/usr/bin/env python3
"""Repository hygiene checks (run in CI):

1. new file names carry a version number and no underscores (exempt: README.md, LICENSE, AGENTS.md, SKILL.md,
   CHANGELOG.md, CITATION.cff, CLAUDE.md, Makefile, package.json, requirements.txt, .gitignore, fixtures README, workflow)
2. no raw subscription-database exports are tracked (inputs/, savedrecs*.txt, *.ciw outside tests/fixtures)
3. the three SKILL.md copies are identical; SKILL.md has name/description front matter
4. every fixture is marked synthetic (10.99999/ DOIs or "合成样例"/"Synthetic" titles)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = {"README.md", "LICENSE", "AGENTS.md", "SKILL.md", "CHANGELOG.md", "CITATION.cff", "CLAUDE.md", "Makefile",
          "package.json", "requirements.txt", ".gitignore", ".nojekyll", "index.html"}
errors = []

files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
if not files:  # not a git checkout: fall back to the tree
    files = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git/" not in str(p) and "node_modules" not in str(p)]
for f in files:
    name = Path(f).name
    if name in EXEMPT or f.startswith("tests/fixtures/README"):
        continue
    if "_" in name:
        errors.append(f"underscore in file name: {f}")
    if not re.search(r"v\d+\.\d+", name):
        errors.append(f"file name lacks a version number: {f}")
    if f.startswith("inputs/") or re.search(r"(savedrecs.*\.txt|\.ciw)$", name) and not f.startswith("tests/fixtures/"):
        errors.append(f"raw database export must not be committed: {f}")

skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
if not re.match(r"^---\nname: [a-z0-9-]+\ndescription: .+?\n---\n", skill, re.S):
    errors.append("SKILL.md lacks name/description front matter")
for mirror in (".agents/skills/mechatlas/SKILL.md", ".claude/skills/mechatlas/SKILL.md"):
    p = ROOT / mirror
    if not p.exists() or p.read_text(encoding="utf-8") != skill:
        errors.append(f"{mirror} differs from SKILL.md (run: make skills)")

for p in (ROOT / "tests" / "fixtures").glob("*"):
    if p.name == "README.md":
        continue
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not ("10.99999/" in txt or "合成样例" in txt or "Synthetic" in txt):
        errors.append(f"fixture not marked synthetic: {p.name}")

for e in errors:
    print("ERROR", e)
print(f"check-repo: {len(files)} files, {len(errors)} problems")
sys.exit(1 if errors else 0)
