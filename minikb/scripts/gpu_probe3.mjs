import { chromium } from 'playwright-core';
const EXE = '/Users/a/Documents/deepseek-harness/default-workspace/.tools/Google Chrome.app/Contents/MacOS/Google Chrome';
const variants = [
  ['--no-sandbox'],
  ['--no-sandbox', '--use-angle=metal'],
  ['--no-sandbox', '--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU'],
  ['--no-sandbox', '--use-gl=angle', '--use-angle=metal', '--enable-unsafe-webgpu'],
];
for (const args of variants) {
  let browser;
  try {
    browser = await chromium.launch({ executablePath: EXE, headless: true, args });
    const page = await browser.newPage();
    const r = await page.evaluate(async () => {
      if (!navigator.gpu) return 'no navigator.gpu';
      const a = await navigator.gpu.requestAdapter();
      if (!a) return 'adapter null';
      return `adapter ok, f16=${a.features.has('shader-f16')}, fallback=${a.isFallbackAdapter}`;
    });
    console.log(args.join(' '), '=>', r);
  } catch (e) {
    console.log(args.join(' '), '=> ERR', String(e.message).slice(0, 120));
  } finally { await browser?.close(); }
}
