const tab = await (await fetch('http://127.0.0.1:9222/json/new?http://127.0.0.1:8787/', { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
const send = (id, method, params) => ws.send(JSON.stringify({ id, method, params }));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(15000); // 等模型加载与示例渲染
send(1, 'Page.captureScreenshot', { format: 'png' });
const msg = await new Promise((resolve) => { ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id === 1) resolve(m); }; });
const b64 = msg.result?.data;
if (!b64) { console.log('screenshot failed', JSON.stringify(msg).slice(0, 200)); process.exit(1); }
const fs = await import('node:fs');
fs.writeFileSync('docs/chrome-screenshot.png', Buffer.from(b64, 'base64'));
console.log('saved docs/chrome-screenshot.png');
ws.close();
