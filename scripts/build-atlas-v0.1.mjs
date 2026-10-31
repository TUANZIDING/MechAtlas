// MechAtlas: build a single-file, light-theme interactive pathway atlas from a registry JSON.
// The page is rendered from data by templates/atlas-template-v0.1.html; nothing is image-model generated.
//
// Usage:
//   node scripts/build-atlas-v0.1.mjs --registry examples/demo/registry-demo-v0.1.json \
//        --studies examples/demo/studies-demo-v0.1.json --out docs/index.html
// Contract (build fails otherwise):
//   - every edge endpoint exists; every edge ref has a source card; every source matches an included study (PMID/DOI)
//   - every evidence node has >= 1 edge; scaffold nodes have none
//   - grades are keys of registry.grades; hero stats are computed from the data, never typed by hand
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true]);
  return acc;
}, []));
const root = resolve(dirname(new URL(import.meta.url).pathname), '..');
const regPath = resolve(args.registry || `${root}/examples/demo/registry-demo-v0.1.json`);
const studiesPath = resolve(args.studies || `${root}/examples/demo/studies-demo-v0.1.json`);
const outPath = resolve(args.out || `${root}/docs/index.html`);
const tplPath = resolve(args.template || `${root}/templates/atlas-template-v0.1.html`);

const reg = JSON.parse(readFileSync(regPath, 'utf8'));
let studies = JSON.parse(readFileSync(studiesPath, 'utf8'));
if (!Array.isArray(studies)) studies = studies.studies || [];

export function check(reg, studies) {
  const errors = [];
  const grades = new Set(Object.keys(reg.grades || { A: 1, B: 1, C: 1 }));
  const ids = new Set();
  for (const n of reg.nodes) {
    if (ids.has(n.id)) errors.push(`duplicate node id ${n.id}`);
    ids.add(n.id);
    if (!['evidence', 'scaffold'].includes(n.status)) errors.push(`node ${n.id}: status must be evidence|scaffold`);
  }
  const sources = Object.fromEntries((Array.isArray(reg.sources) ? reg.sources : Object.values(reg.sources)).map(s => [s.id, s]));
  const norm = d => (d || '').toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, '');
  const incPmid = new Set(studies.filter(s => s.pmid).map(s => String(s.pmid)));
  const incDoi = new Set(studies.filter(s => s.doi).map(s => norm(s.doi)));
  for (const s of Object.values(sources)) {
    if (!s.pmid && !s.doi && !s.url) errors.push(`source ${s.id} has no PMID, DOI or URL`);
    if (!(s.pmid && incPmid.has(String(s.pmid))) && !(s.doi && incDoi.has(norm(s.doi)))) errors.push(`source ${s.id} is not an included study`);
  }
  const eids = new Set();
  for (const e of reg.edges) {
    if (eids.has(e.id)) errors.push(`duplicate edge id ${e.id}`);
    eids.add(e.id);
    if (!ids.has(e.source) || !ids.has(e.target)) errors.push(`dangling edge ${e.id}`);
    if (!grades.has(e.grade)) errors.push(`edge ${e.id}: grade ${e.grade} not in registry.grades`);
    if (!['+', '-'].includes(e.direction)) errors.push(`edge ${e.id}: direction must be + or -`);
    if (!e.refs || !e.refs.length) errors.push(`edge ${e.id} has no included source`);
    for (const r of e.refs || []) if (!sources[r]) errors.push(`edge ${e.id}: missing source card ${r}`);
  }
  const linked = new Set(reg.edges.flatMap(e => [e.source, e.target]));
  for (const n of reg.nodes) {
    if (n.status === 'evidence' && !linked.has(n.id)) errors.push(`evidence node ${n.id} has no edge`);
    if (n.status === 'scaffold' && linked.has(n.id)) errors.push(`scaffold node ${n.id} must not carry evidence edges`);
  }
  return { errors, sources };
}

const { errors, sources } = check(reg, studies);
if (errors.length) { console.error(errors.join('\n')); process.exit(1); }

// group parallel relations (same source>target) into one drawn edge; strongest grade first
const RANK = Object.fromEntries(Object.keys(reg.grades || { A: 1, B: 1, C: 1 }).map((g, i) => [g, i]));
const m = new Map();
for (const e of reg.edges) {
  const key = `${e.source}>${e.target}`;
  const rec = { id: e.id, grade: e.grade, kind: e.kind, causal: e.causal, sign: e.direction, label: e.label, refs: e.refs,
    model: e.model, doseText: e.dose, time: e.time, method: e.method, functional: e.functional, limitation: e.limitation };
  if (!m.has(key)) m.set(key, { key, source: e.source, target: e.target, items: [rec] }); else m.get(key).items.push(rec);
}
const edges = [...m.values()].map(g => {
  g.items.sort((a, b) => RANK[a.grade] - RANK[b.grade]);
  g.grade = g.items[0].grade; g.inhibit = g.items.some(i => i.sign === '-'); g.ids = g.items.map(i => i.id); return g;
});
const count = g => studies.filter(s => s.grade === g).length;
const stats = { included: studies.length, A: count('A'), B: count('B'), C: count('C'), sources: Object.keys(sources).length,
  nodes: reg.nodes.length, relations: reg.edges.length, drawn: edges.length, scaffold: reg.nodes.filter(n => n.status === 'scaffold').length,
  abstractOnly: studies.filter(s => s.abstract_only).length };
const DATA = { version: reg.version, versionLabel: reg.version, generated: reg.generated, title: reg.title, subtitle: reg.subtitle,
  changelog: reg.changelog, grades: reg.grades, stats, lanes: reg.lanes, cols: reg.cols, rows: reg.rows, scaffoldTop: reg.scaffoldTop || reg.rows,
  focusNode: reg.focusNode || null, links: reg.links || [], nodes: reg.nodes, edges, sources };
const tpl = readFileSync(tplPath, 'utf8');
const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const zh = o => (o && typeof o === 'object' ? (o.zh || o.en) : o) || '';
const html = tpl
  .replace('__TITLE__', esc(`${zh(reg.title)} · ${reg.version}`))
  .replace('__DESC__', esc(`${zh(reg.title)}：交互式信号通路证据图（A/B/C 分级，${stats.included} 篇纳入研究）`))
  .replace('/*__DATA__*/null', JSON.stringify(DATA).replace(/</g, '\\u003c'));
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, html);
console.log(`atlas ${reg.version} -> ${outPath}: ${JSON.stringify(stats)}`);
