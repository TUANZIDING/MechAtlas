# MechAtlas (English overview)

**MechAtlas turns a biomedical topic into a verified, evidence-graded, interactive signalling-pathway atlas.** It is an agent skill (`SKILL.md`, `AGENTS.md`) plus a small toolkit (Python standard library + PyYAML; Node + Playwright) for Codex, Claude Code and similar coding agents.

Live demo: <https://tuanziding.github.io/MechAtlas/> · Chinese homepage: [README.md](../README.md)

## Pipeline

1. **Search** (high recall): PubMed (E-utilities history server), Europe PMC (incl. preprints), OpenAlex, Crossref, Semantic Scholar, bioRxiv/medRxiv, plus manual exports from Web of Science, Scopus, Embase, CNKI and Wanfang (`import-export-v0.1.py`). One runner (`run-search-v0.1.py`) reads the topic YAML.
2. **Dedupe + PRISMA**: DOI → PMID → normalised title; PRISMA 2020-style counts, markdown and SVG.
3. **Screening with reasons**: a CSV sheet (title/abstract, full text, abstract-only flag); judgments survive refreshes.
4. **Snowballing**: references + cited-by (OpenAlex, or Semantic Scholar with `--backend s2`) until a round adds fewer than 3 included studies.
5. **A/B/C grading**: A = direct model of the topic; B = adjacent/non-target model or conditional/abstract-level; C = inference/candidate/indirect. Conflicting or hypothesis-only mechanisms become scaffold nodes without edges. Definitions are configurable per topic.
6. **Registry JSON** (`schema/registry-v0.1.schema.json`): lanes, nodes, edges with direction, grade, causal flag, refs, model, dose, time, limitation.
7. **Atlas**: a single-file, light-theme HTML rendered from the JSON by `build-atlas-v0.1.mjs` (never image-model generated); stats computed from data.
8. **Verification**: every PMID/DOI resolved via API with title matching (`verify-refs-v0.1.py`); evidence contract (`validate-registry-v0.1.py`); Playwright real clicks on every node, grade filters, console errors = [], no overlaps, 390 px mobile without horizontal overflow (`verify-atlas-v0.1.mjs`).
9. **Report**: a Chinese, conclusion-first report skeleton (`report-v0.1.py`), each claim tagged fact / association / speculation.

## Gated loops and human approval (v0.2)

`pipeline-v0.1.py run|status|approve` drives the stages as a resumable state machine (`run-state-v0.1.json`; exit codes 0 done, 10 needs approval, 11 needs agent work, 12 waiting to retry). Gates:

- **Retrieval**: an error is retried with backoff (default 3 tries, ~1/5/15 min), then a human chooses skip / change query / switch source. A successful search with 0 hits is not retried; it goes straight to a human (it may be a real evidence gap). Nothing is skipped silently.
- **Extraction coverage**: every included study maps to ≥ 1 edge or carries a recorded reason in `unmapped[]` (layout is never a reason); up to 2 re-extraction rounds, then escalation.
- **Render consistency**: the atlas's embedded data is compared one-to-one with the registry, plus static geometry; up to 3 re-layouts (rows only, never dropping edges or merging nodes), then escalation.
- **Full-text upgrade**: abstract-only studies are looked up in Europe PMC and Unpaywall; retrieved texts are re-read and grade changes proposed.
- At most 2 outer loops. Humans approve the search-strategy freeze, any exhausted gate, each grade change / edge deletion / node merge, and the final release. Approvals are bound to fingerprints of their inputs.

## Rules that never bend

- Never fabricate references; every included record is a locally saved API-fetched record.
- Mark abstract-only studies; grade A requires full text.
- Raw exports from subscription databases are licensed content: keep them in `inputs/` (gitignored); publish only identifiers and screening judgments. Agents cannot log in to institutional web subscriptions.

## Status (v0.1, 2026-11-01)

PubMed, Europe PMC, Semantic Scholar, Crossref and bioRxiv scripts were run live on small queries. OpenAlex now meters anonymous use per IP; set `OPENALEX_API_KEY`. Importers are tested on synthetic fixtures (not real exports). The WoS Starter and Elsevier API adapters are untested without keys.

## License

MIT. See [LICENSE](../LICENSE) and [CITATION.cff](../CITATION.cff).
