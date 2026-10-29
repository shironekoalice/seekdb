import { chromium } from 'playwright-core';
const EXE = '/Users/a/Documents/deepseek-harness/default-workspace/.tools/Google Chrome.app/Contents/MacOS/Google Chrome';
for (const headless of [true, false]) {
  let browser;
  try {
    browser = await chromium.launch({ executablePath: EXE, headless, args: ['--enable-unsafe-webgpu'] });
    const page = await browser.newPage();
    const info = await page.evaluate(async () => {
      if (!navigator.gpu) return { gpu: false };
      const adapter = await navigator.gpu.requestAdapter();
      if (!adapter) return { gpu: true, adapter: null };
      const d = adapter.info || {};
      return {
        gpu: true,
        adapter: true,
        vendor: d.vendor, architecture: d.architecture, device: d.device, description: d.description,
        features: [...adapter.features].filter((f) => /f16|timestamp/.test(f)),
        isFallback: adapter.isFallbackAdapter,
      };
    });
    console.log(`headless=${headless}`, JSON.stringify(info));
  } catch (e) {
    console.log(`headless=${headless} ERROR`, String(e.message).slice(0, 200));
  } finally {
    await browser?.close();
  }
}
