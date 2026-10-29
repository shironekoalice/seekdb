import { chromium } from 'playwright-core';

const B = 'http://127.0.0.1:8787';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

await page.goto(B + '/?backend=wasm', { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => !document.getElementById('add').disabled, null, { timeout: 240000 });
const before = Number(await page.textContent('#count'));

const title = '北极狐的换毛';
const text = '北极狐的毛色会随季节变化：冬天全身雪白，和雪地融为一体；夏天换成灰褐色，便于在苔原上伪装。这种换毛由光照周期驱动。';
await page.fill('#title', title);
await page.fill('#text', text);

const t0 = Date.now();
await page.click('#add');
await page.waitForFunction((n) => Number(document.getElementById('count').textContent) === n + 1, before, { timeout: 120000 });
const importMs = Date.now() - t0;
await page.screenshot({ path: 'docs/demo-1-import.png', fullPage: true });

// 从 seekdb 里把这条连同向量取回来
const docs = await page.evaluate(() => fetch('/api/docs?collection=notes').then((r) => r.json()));
const item = docs.items.find((d) => d.title === '北极狐的换毛');
const raw = await page.evaluate((id) => fetch(`/api/docs/${id}?collection=notes`).then((r) => r.json()), item.id);

// 语义检索：查询措辞和原文几乎没有重叠词
await page.click('#modes button[data-mode="semantic"]');
await page.fill('#q', '哪种动物的毛色会随季节改变？');
const t1 = Date.now();
await page.click('#search');
await page.waitForFunction(() => document.querySelectorAll('#results .card').length > 0, null, { timeout: 60000 });
const searchMs = Date.now() - t1;
const ranked = await page.$$eval('#results .card', (els) =>
  els.map((e) => ({ title: e.querySelector('.title')?.textContent, score: e.querySelector('.score')?.textContent })));
const meta = await page.textContent('#search-meta');

// 关键词模式对照
await page.click('#modes button[data-mode="keyword"]');
await page.click('#search');
await page.waitForFunction(() => (document.getElementById('search-meta')?.textContent || '').includes('seekdb'), null, { timeout: 60000 });
const kwRanked = await page.$$eval('#results .card', (els) =>
  els.map((e) => ({ title: e.querySelector('.title')?.textContent, score: e.querySelector('.score')?.textContent })));

await page.click('#modes button[data-mode="semantic"]');
await page.click('#search');
await page.waitForFunction(() => document.querySelectorAll('#results .card').length > 0, null, { timeout: 60000 });
await page.screenshot({ path: 'docs/demo-2-search.png', fullPage: true });

console.log(JSON.stringify({
  before, after: Number(await page.textContent('#count')), importMs,
  stored: { id: raw.id, dim: raw.dim, vectorPreview: raw.vectorPreview, vectorNorm: raw.vectorNorm, title: raw.title },
  query: '哪种动物的毛色会随季节改变？',
  semantic: { ms: searchMs, meta, ranked },
  keyword: { ranked: kwRanked },
  errors,
}, null, 2));
await browser.close();
