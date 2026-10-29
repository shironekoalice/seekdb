import fs from 'node:fs';

const tab = await (await fetch('http://127.0.0.1:9222/json/new?http://127.0.0.1:8787/', { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));

let seq = 0;
const pending = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
};
function evalJs(expression, awaitPromise = false) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise, returnByValue: true } }));
  return new Promise((resolve) => pending.set(id, (m) => resolve(m.result?.result?.value)));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(expr, timeout = 240000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await evalJs(expr)) return true;
    await sleep(500);
  }
  return false;
}

await waitFor('!!document.getElementById("add") && !document.getElementById("add").disabled');
const gpu = await evalJs(`(async () => {
  const a = await navigator.gpu?.requestAdapter();
  return a ? { f16: a.features.has('shader-f16'), vendor: a.info?.vendor, arch: a.info?.architecture } : null;
})()` , true);
const badge = await evalJs('document.getElementById("backend-badge").textContent');
console.log('backend:', badge, '| adapter:', JSON.stringify(gpu));

// 确保有示例
const count = await evalJs('Number(document.getElementById("count").textContent)');
if (count < 6) { await evalJs('document.getElementById("seed").click()'); await waitFor('Number(document.getElementById("count").textContent) >= 6'); }

// 跑性能面板
await evalJs('document.getElementById("bench-box").open = true');
await evalJs('document.getElementById("bench-run").click()');
await waitFor('(document.getElementById("bench-out").textContent||"").includes("modelLoadMs")', 180000);
const bench = JSON.parse(await evalJs('document.getElementById("bench-out").textContent'));

// 三种检索
const out = {};
for (const mode of ['hybrid', 'semantic', 'keyword']) {
  await evalJs(`document.querySelector('#modes button[data-mode="${mode}"]').click()`);
  await evalJs(`document.getElementById('q').value = 'seekdb 的混合检索怎么排序？'; document.getElementById('search').click()`);
  await waitFor('document.querySelectorAll("#results .card").length > 0', 60000);
  out[mode] = {
    meta: await evalJs('document.getElementById("search-meta").textContent'),
    top: await evalJs(`[...document.querySelectorAll('#results .card')].slice(0,3).map(e => e.querySelector('.title').textContent + ' | ' + e.querySelector('.score').textContent)`),
  };
  await sleep(300);
}
console.log('search:', JSON.stringify(out, null, 1));
console.log('BENCH:', JSON.stringify(bench));
fs.writeFileSync('docs/bench-webgpu.json', JSON.stringify({ backend: badge, adapter: gpu, bench, search: out }, null, 2));

// 截图
ws.send(JSON.stringify({ id: ++seq, method: 'Page.captureScreenshot', params: { format: 'png' } }));
const shot = await new Promise((resolve) => pending.set(seq, (m) => resolve(m.result?.data)));
if (shot) fs.writeFileSync('docs/chrome-webgpu.png', Buffer.from(shot, 'base64'));
ws.close();
