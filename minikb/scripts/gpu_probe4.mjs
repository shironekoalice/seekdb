import { chromium } from 'playwright-core';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
const ctx = browser.contexts()[0] ?? (await browser.newContext());
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:8787/', { waitUntil: 'domcontentloaded' });
const r = await page.evaluate(async () => {
  const out = { hasGpu: !!navigator.gpu };
  if (!navigator.gpu) return out;
  const a = await navigator.gpu.requestAdapter();
  if (!a) { out.adapter = null; return out; }
  out.adapter = true;
  out.f16 = a.features.has('shader-f16');
  out.info = a.info ? { vendor: a.info.vendor, architecture: a.info.architecture, device: a.info.device, description: a.info.description } : null;
  out.fallback = a.isFallbackAdapter;
  return out;
});
console.log(JSON.stringify(r));
await page.close();
await browser.close();
