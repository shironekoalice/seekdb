import { chromium } from 'playwright-core';
import fs from 'node:fs';

const B = 'http://127.0.0.1:8787';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const logs = [];
page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));

await page.goto(B + '/?backend=wasm', { waitUntil: 'domcontentloaded' });
console.log('title:', await page.title());

// 等模型就绪（首次要下 24MB，走本机代理）
try {
  await page.waitForFunction(() => {
    const t = document.getElementById('backend-badge')?.textContent || '';
    return /ok|WebGPU|WASM/.test(t) && !/加载中|下载/.test(t);
  }, null, { timeout: 240000 });
} catch (e) {
  console.log('等待模型超时，badge=', await page.textContent('#backend-badge'));
}
console.log('backend badge:', await page.textContent('#backend-badge'));
console.log('model badge:', await page.textContent('#model-badge'));

// 载入示例
console.log('before seed: add disabled=', await page.$eval('#add', (b) => b.disabled));
await page.click('#seed', { timeout: 60000 });
await page.waitForFunction(() => Number(document.getElementById('count')?.textContent) >= 6, null, { timeout: 120000 });
console.log('docs count:', await page.textContent('#count'));

// 三种模式各搜一次
const out = {};
for (const mode of ['hybrid', 'semantic', 'keyword']) {
  await page.click(`#modes button[data-mode="${mode}"]`);
  await page.fill('#q', 'seekdb 的混合检索怎么排序？');
  const t0 = Date.now();
  await page.click('#search');
  await page.waitForFunction(() => (document.querySelectorAll('#results .card').length > 0) || document.getElementById('search-meta')?.textContent?.includes('ms'), null, { timeout: 60000 });
  out[mode] = {
    cards: await page.$$eval('#results .card', (els) => els.map((e) => e.querySelector('.title')?.textContent + ' | ' + e.querySelector('.score')?.textContent)),
    meta: await page.textContent('#search-meta'),
    wall: Date.now() - t0,
  };
}
console.log(JSON.stringify(out, null, 2));

// 性能面板
await page.click('#bench-box > summary');
await page.click('#bench-run');
await page.waitForFunction(() => (document.getElementById('bench-out')?.textContent || '').includes('modelLoadMs'), null, { timeout: 180000 });
const bench = await page.textContent('#bench-out');
console.log('BENCH', bench);

await page.screenshot({ path: 'docs/screenshot.png', fullPage: true });
fs.writeFileSync('docs/e2e-result.json', JSON.stringify({ backend: await page.textContent('#backend-badge'), searches: out, bench: JSON.parse(bench), logs }, null, 2));

const errs = logs.filter((l) => l.startsWith('[pageerror]') || l.startsWith('[error]'));
console.log('errors:', errs.slice(0, 5));
await browser.close();
