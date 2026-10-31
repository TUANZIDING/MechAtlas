// MechAtlas: README screenshots rendered from the real page (Playwright, light theme, DPR 2).
// Usage: CHROMIUM_PATH=/usr/bin/chromium node scripts/shots-atlas-v0.1.mjs --html docs/index.html --outdir docs/assets \
//          --node mpcEV --edge "hdacFap>mir206"
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true]);
  return acc;
}, []));
const root = resolve(dirname(new URL(import.meta.url).pathname), '..');
const url = pathToFileURL(resolve(args.html || `${root}/docs/index.html`)).href;
const outdir = resolve(args.outdir || `${root}/docs/assets`);
const node = args.node || 'mpcEV';
const edge = args.edge || 'hdacFap>mir206';
mkdirSync(outdir, { recursive: true });
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });

const d = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'light' });
await d.goto(url); await d.waitForTimeout(800);
await d.locator(`.node[data-node="${node}"]`).click(); await d.waitForTimeout(300);
await d.screenshot({ path: `${outdir}/hero-v0.1.png` });
await d.locator('#svg').click({ position: { x: 6, y: 6 } });
await d.locator(`.edge[data-edge="${edge}"] path.hit`).click({ force: true }); await d.waitForTimeout(300);
const clip = await d.evaluate(() => { const side = document.querySelector('#side').getBoundingClientRect(); const kids = [...document.querySelectorAll('#inspector > *')]; const bottom = Math.max(...kids.map(k => k.getBoundingClientRect().bottom)); return { x: side.x, y: side.y, width: side.width, height: Math.min(side.height, bottom - side.y + 28) }; });
await d.screenshot({ path: `${outdir}/inspector-v0.1.png`, clip });

const m = await b.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: 'light' });
await m.goto(url); await m.waitForTimeout(800);
await m.locator(`.node[data-node="${node}"]`).click(); await m.waitForTimeout(300);
await m.screenshot({ path: `${outdir}/mobile-390-v0.1.png` });
await b.close();
console.log(`screenshots -> ${outdir}: hero-v0.1.png, inspector-v0.1.png, mobile-390-v0.1.png`);
