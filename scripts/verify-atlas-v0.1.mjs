// MechAtlas: verify a built atlas with REAL Playwright clicks (locator.click), not synthetic events.
// Checks: header stats derived from data; every node clickable (evidence -> its PMIDs/DOIs, scaffold -> note);
// one edge click per grade; grade filters; language toggle; console/page errors == [];
// layout geometry (no node overlap, no edge through a non-endpoint node, no badge on a node, no title overflow,
// lane labels fit, nodes inside their lane); desktop + 390 px mobile with no horizontal overflow.
//
// Usage:
//   CHROMIUM_PATH=/usr/bin/chromium node scripts/verify-atlas-v0.1.mjs --html docs/index.html \
//     --registry examples/demo/registry-demo-v0.1.json --studies examples/demo/studies-demo-v0.1.json \
//     --out docs/demo/validation-v0.1.json
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true]);
  return acc;
}, []));
const root = resolve(dirname(new URL(import.meta.url).pathname), '..');
const htmlPath = resolve(args.html || `${root}/docs/index.html`);
const reg = JSON.parse(readFileSync(resolve(args.registry || `${root}/examples/demo/registry-demo-v0.1.json`), 'utf8'));
let studies = JSON.parse(readFileSync(resolve(args.studies || `${root}/examples/demo/studies-demo-v0.1.json`), 'utf8'));
if (!Array.isArray(studies)) studies = studies.studies || [];
const outPath = resolve(args.out || `${root}/docs/demo/validation-v0.1.json`);
const url = pathToFileURL(htmlPath).href;
const sources = Object.fromEntries((Array.isArray(reg.sources) ? reg.sources : Object.values(reg.sources)).map(s => [s.id, s]));
const tag = s => (s.pmid ? `PMID ${s.pmid}` : s.doi ? `DOI ${s.doi}` : s.id);

const checks = [];
const ok = (name, pass, detail = '') => { checks.push({ name, pass: !!pass, detail: String(detail).slice(0, 300) }); console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail && !pass ? '  — ' + String(detail).slice(0, 200) : ''}`); };

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const errs = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
await page.goto(url); await page.waitForTimeout(700);

const cnt = g => studies.filter(s => s.grade === g).length;
const hdr = await page.locator('#hdrStats').textContent();
ok('header stats derived from data', hdr.includes(`纳入 ${studies.length} 篇`) && hdr.includes(`A ${cnt('A')} / B ${cnt('B')} / C ${cnt('C')}`), hdr);
ok('node count rendered = registry', await page.locator('.node').count() === reg.nodes.length, `${await page.locator('.node').count()} / ${reg.nodes.length}`);
const drawn = new Set(reg.edges.map(e => `${e.source}>${e.target}`)).size;
ok('edge count rendered = grouped relations', await page.locator('.edge').count() === drawn, `${await page.locator('.edge').count()} / ${drawn}`);

// every node: real click, inspector must show each linked source tag (evidence) or the scaffold note
const empty = async () => page.locator('#svg').click({ position: { x: 6, y: 6 } });
let nodeFails = [];
for (const n of reg.nodes) {
  await page.locator(`.node[data-node="${n.id}"]`).click();
  const txt = await page.locator('#inspector').textContent();
  if (/\[object Object\]|undefined|NaN/.test(txt)) nodeFails.push(`${n.id}: bad text in inspector`);
  if (n.status === 'scaffold') { if (!txt.includes('证据待补')) nodeFails.push(n.id); continue; }
  const refs = [...new Set(reg.edges.filter(e => e.source === n.id || e.target === n.id).flatMap(e => e.refs))];
  const missing = refs.filter(r => !txt.includes(tag(sources[r])));
  if (missing.length) nodeFails.push(`${n.id}:${missing.join(',')}`);
}
ok(`real click on all ${reg.nodes.length} nodes shows their sources / scaffold note`, nodeFails.length === 0, nodeFails.join('; '));

// links in the inspector go to PubMed / doi.org
const hrefs = await page.locator('#inspector a[data-ref]').evaluateAll(as => as.map(a => a.href));
await empty();

// one edge per grade
for (const g of Object.keys(reg.grades || { A: 1, B: 1, C: 1 })) {
  const e = reg.edges.find(x => x.grade === g);
  if (!e) continue;
  await page.locator(`.edge[data-edge="${e.source}>${e.target}"] path.hit`).click({ force: true });
  const txt = await page.locator('#inspector').textContent();
  ok(`click grade-${g} edge ${e.id} shows its label and sources`, e.refs.every(r => txt.includes(tag(sources[r]))) && txt.includes(e.id), txt.slice(0, 120));
  const linkOk = await page.locator('#inspector a[data-ref]').evaluateAll(as => as.every(a => /^https:\/\/(pubmed\.ncbi\.nlm\.nih\.gov\/\d+\/|doi\.org\/10\.)/.test(a.href)));
  ok(`grade-${g} evidence links point to PubMed / doi.org`, linkOk);
  await empty();
}
ok('click on empty canvas clears selection', (await page.locator('#inspector .empty').count()) === 1);

// grade filters
for (const g of Object.keys(reg.grades || {})) {
  const btn = page.locator(`[data-f="${g}"]`);
  if (!(await btn.count())) continue;
  await btn.click();
  const vis = await page.locator('.edge:not(.dim)').count();
  const exp = new Set(reg.edges.filter(e => e.grade === g).map(e => `${e.source}>${e.target}`)).size;
  ok(`filter ${g} shows only grade-${g} relations`, vis === exp, `${vis} visible / ${exp} expected`);
}
await page.locator('[data-f="all"]').click();
ok('filter All restores every edge', await page.locator('.edge:not(.dim)').count() === drawn);

await page.locator('#fLang').click();
ok('EN toggle', (await page.locator('#hdrStats').textContent()).includes(`${studies.length} studies`));
await page.locator('#fLang').click();
await page.locator('#fSrc').click();
ok('sources dialog lists every source', (await page.locator('#srcGrid .srccard').count()) === Object.keys(sources).length);
await page.locator('#srcClose').click();

// geometry
const geo = await page.evaluate(() => {
  const G = window.__ATLAS_GEOM; const { NW, NH, CW, RH, PADX, PADY } = G;
  const out = { nodeOverlap: [], edgeThroughNode: [], badgeOnNode: [], textOverflow: [], laneLabelOverflow: [], nodeOutsideLane: [] };
  const rects = DATA.nodes.map(n => ({ id: n.id, x: PADX + n.c * CW, y: PADY + n.r * RH, r: n.r }));
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) { const a = rects[i], c = rects[j]; if (a.x < c.x + NW + 4 && c.x < a.x + NW + 4 && a.y < c.y + NH + 4 && c.y < a.y + NH + 4) out.nodeOverlap.push(a.id + '/' + c.id); }
  document.querySelectorAll('.edge').forEach(g => {
    const [s, t] = g.dataset.edge.split('>'); const w = g.querySelector('path.wire'); const L = w.getTotalLength();
    for (let k = 1; k < 100; k++) { const pt = w.getPointAtLength(L * k / 100); const hit = rects.find(n => n.id !== s && n.id !== t && pt.x > n.x - 3 && pt.x < n.x + NW + 3 && pt.y > n.y - 3 && pt.y < n.y + NH + 3); if (hit) { out.edgeThroughNode.push(g.dataset.edge + ' x ' + hit.id); break; } }
    const bg = g.querySelector('.edge-badge'); const m = bg.getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/); const bx = +m[1], by = +m[2], bw = +bg.querySelector('rect').getAttribute('width');
    rects.forEach(n => { if (bx + bw / 2 > n.x && bx - bw / 2 < n.x + NW && by + 10 > n.y && by - 10 < n.y + NH) out.badgeOnNode.push(g.dataset.edge + ' on ' + n.id); });
  });
  document.querySelectorAll('.node').forEach(g => { g.querySelectorAll('.tt').forEach(t => { if (t.getComputedTextLength() > NW - 36) out.textOverflow.push(g.dataset.node); }); const st = g.querySelector('.st'); if (st && st.getComputedTextLength() > NW - 20) out.textOverflow.push(g.dataset.node + ' sub'); });
  document.querySelectorAll('[data-lane-label]').forEach(t => { const L = DATA.lanes.find(x => x.id === t.dataset.laneLabel); if (t.getComputedTextLength() > (L.r1 - L.r0) * RH - 24) out.laneLabelOverflow.push(L.id); });
  for (const n of rects) { const L = DATA.lanes.find(x => n.r >= x.r0 && n.r < x.r1); if (!L) { out.nodeOutsideLane.push(n.id); continue; } const top = PADY + L.r0 * RH - 28 + 4, bot = PADY + L.r1 * RH - 28 - 4; if (n.y < top || n.y + NH > bot) out.nodeOutsideLane.push(n.id); }
  return out;
});
for (const [k, v] of Object.entries(geo)) ok(`layout: ${k} = 0`, v.length === 0, v.slice(0, 6).join('; '));
ok('desktop: no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
ok('desktop: console/page errors = []', errs.length === 0, errs.join(' | '));

// mobile 390 px
const m = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const merrs = []; m.on('pageerror', e => merrs.push(String(e))); m.on('console', x => { if (x.type() === 'error') merrs.push(x.text()); });
await m.goto(url); await m.waitForTimeout(700);
ok('390 px: no horizontal overflow', await m.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth));
const first = reg.nodes.find(n => n.status === 'evidence');
await m.locator('#fFit').click();
await m.locator(`.node[data-node="${first.id}"]`).click();
const ref0 = reg.edges.find(e => e.source === first.id || e.target === first.id).refs[0];
ok(`390 px: real click on node ${first.id} opens inspector`, (await m.locator('#inspector').textContent()).includes(tag(sources[ref0])));
ok('390 px: toolbar fits (no button clipped off-screen without scroll)', await m.evaluate(() => { const tb = document.querySelector('.toolbar'); return tb.scrollWidth >= tb.clientWidth && tb.getBoundingClientRect().right <= innerWidth + 1; }));
ok('390 px: console/page errors = []', merrs.length === 0, merrs.join(' | '));
await browser.close();

const failed = checks.filter(c => !c.pass).length;
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify({ atlas: reg.version, html: htmlPath.replace(root + '/', ''), date: new Date().toLocaleDateString('sv-SE'),
  passed: checks.length - failed, failed, pageErrors: errs.concat(merrs), checks }, null, 2));
console.log(`${checks.length - failed}/${checks.length} checks passed -> ${outPath.replace(root + '/', '')}`);
process.exit(failed ? 1 : 0);
