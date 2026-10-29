const url = 'http://127.0.0.1:9222/json/new?http://127.0.0.1:8787/';
const tab = await (await fetch(url, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
const expr = `(async () => {
  if (!navigator.gpu) return { gpu: false };
  const a = await navigator.gpu.requestAdapter();
  if (!a) return { gpu: true, adapter: false };
  return { gpu: true, adapter: true, f16: a.features.has('shader-f16'), fallback: a.isFallbackAdapter,
           info: a.info ? { vendor: a.info.vendor, architecture: a.info.architecture, device: a.info.device } : null };
})()`;
ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
const result = await new Promise((resolve) => {
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.id === 1) resolve(msg);
  };
});
console.log(JSON.stringify(result.result?.result?.value ?? result));
ws.close();
