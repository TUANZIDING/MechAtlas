# MechAtlas shortcuts. CHROMIUM_PATH=/usr/bin/chromium uses a system Chromium instead of Playwright's.
PY ?= python3
REG ?= examples/demo/registry-demo-v0.1.json
STU ?= examples/demo/studies-demo-v0.1.json
OUT ?= docs/index.html

.PHONY: test demo gates verify shots refs smoke skills check

test:            ## unit tests (offline)
	$(PY) tests/run-tests-v0.1.py

demo:            ## validate + build the demo atlas into docs/index.html
	$(PY) scripts/validate-registry-v0.1.py --registry $(REG) --studies $(STU)
	node scripts/build-atlas-v0.1.mjs --registry $(REG) --studies $(STU) --out $(OUT)

gates: demo      ## render-consistency + static geometry gates on the built atlas (offline)
	$(PY) scripts/gates-v0.1.py render --registry $(REG) --html $(OUT)
	$(PY) scripts/gates-v0.1.py geometry --registry $(REG)

verify: demo     ## real-click Playwright verification (desktop + 390 px)
	node scripts/verify-atlas-v0.1.mjs --html $(OUT) --registry $(REG) --studies $(STU) --out docs/demo/validation-v0.1.json

shots: demo      ## README screenshots, DPR 2
	node scripts/shots-atlas-v0.1.mjs --html $(OUT) --outdir docs/assets

refs:            ## resolve every PMID/DOI in the demo registry (network)
	$(PY) scripts/verify-refs-v0.1.py $(REG) --out docs/demo/verify-refs-v0.1.json

smoke:           ## 3 records per query from PubMed + Europe PMC (network)
	$(PY) scripts/run-search-v0.1.py examples/radiation-muscle/topic-v0.1.yaml --max 3 --only pubmed,europepmc --run-dir runs/smoke

skills:          ## mirror SKILL.md into .agents/ and .claude/
	cp SKILL.md .agents/skills/mechatlas/SKILL.md && cp SKILL.md .claude/skills/mechatlas/SKILL.md

check: test gates verify
