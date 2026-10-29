/**
 * 按赛题 3.2 的口径采集性能数据：
 * - 冷启动 / 热启动模型加载分别计时
 * - 单条与批量 embedding、写入到可见、查询 embedding、seekdb 检索、端到端
 * - 每项重复 5 次，报告 median / min / max
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';

const B = 'http://127.0.0.1:8787';
const ROUNDS = 5;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

const ready = () => page.waitForFunction(() => !document.getElementById('add').disabled, null, { timeout: 300000 });

// 冷启动
let t0 = Date.now();
await page.goto(B + '/?backend=wasm', { waitUntil: 'domcontentloaded' });
await ready();
const coldLoadMs = Date.now() - t0;
const backend = await page.textContent('#backend-badge');

// 热启动（浏览器已缓存模型）
t0 = Date.now();
await page.reload({ waitUntil: 'domcontentloaded' });
await ready();
const warmLoadMs = Date.now() - t0;

// 保证有 6 条示例
const count = Number(await page.textContent('#count'));
if (count < 6) {
  await page.click('#seed');
  await page.waitForFunction(() => Number(document.getElementById('count').textContent) >= 6, null, { timeout: 120000 });
}

const rounds = [];
for (let i = 0; i < ROUNDS; i++) {
  // 面板基准
  await page.evaluate(() => { document.getElementById('bench-box').open = true; });
  await page.click('#bench-run');
  await page.waitForFunction(() => (document.getElementById('bench-out')?.textContent || '').includes('modelLoadMs'), null, { timeout: 180000 });
  const bench = JSON.parse(await page.textContent('#bench-out'));

  // 端到端检索：语义模式，计时到结果渲染完成
  await page.click('#modes button[data-mode="semantic"]');
  await page.fill('#q', 'seekdb 的混合检索怎么排序？');
  const e2e = await page.evaluate(async () => {
    const t = performance.now();
    document.getElementById('search').click();
    await new Promise((resolve) => {
      const check = () => (document.querySelectorAll('#results .card').length ? resolve() : requestAnimationFrame(check));
      check();
    });
    const wall = performance.now() - t;
    const meta = document.getElementById('search-meta').textContent || '';
    return { wall, meta };
  });
  const m = e2e.meta.match(/embedding ([\d.]+) ms · seekdb ([\d.]+) ms/);

  // 写入到可见
  const add = await page.evaluate(async (n) => {
    document.getElementById('title').value = `基准文档 ${n}`;
    document.getElementById('text').value = `这是第 ${n} 次基准写入，用于测量从点击到列表可见的耗时。`;
    const before = Number(document.getElementById('count').textContent);
    const t = performance.now();
    document.getElementById('add').click();
    await new Promise((resolve) => {
      const check = () => (Number(document.getElementById('count').textContent) > before ? resolve() : requestAnimationFrame(check));
      check();
    });
    return performance.now() - t;
  }, i + 1);

  rounds.push({
    modelLoadMs: bench.modelLoadMs,
    embedSingleMs: bench.embedSingleMs,
    batch8PerItemMs: bench.batch8PerItemMs,
    dbSearchMs: bench.dbSearchMs,
    queryEmbedMs: m ? Number(m[1]) : null,
    queryDbMs: m ? Number(m[2]) : null,
    e2eMs: Math.round(e2e.wall),
    addToVisibleMs: Math.round(add),
  });
}

const med = (xs) => {
  const a = xs.filter((x) => typeof x === 'number').sort((p, q) => p - q);
  if (!a.length) return null;
  const mid = Math.floor(a.length / 2);
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
};
const stat = (key) => {
  const xs = rounds.map((r) => r[key]).filter((x) => typeof x === 'number');
  return { median: +med(xs).toFixed(2), min: +Math.min(...xs).toFixed(2), max: +Math.max(...xs).toFixed(2), n: xs.length };
};

const out = {
  env: { browser: await browser.version(), backend, rounds: ROUNDS, docs: await page.textContent('#count') },
  modelLoad: { coldMs: coldLoadMs, warmMs: warmLoadMs },
  embedSingle: stat('embedSingleMs'),
  batch8PerItem: stat('batch8PerItemMs'),
  queryEmbed: stat('queryEmbedMs'),
  queryDb: stat('queryDbMs'),
  e2e: stat('e2eMs'),
  addToVisible: stat('addToVisibleMs'),
  raw: rounds,
  errors,
};
fs.writeFileSync('docs/bench.json', JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
await browser.close();
