const tabs = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const tab = tabs.find((t) => t.url.includes('127.0.0.1:8787'));
if (!tab) { console.log('no app tab'); process.exit(1); }
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let seq = 0; const pending = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const ev = (expr) => { const id = ++seq; ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression: expr, returnByValue: true } })); return new Promise((r) => pending.set(id, (m) => r(m.result?.result?.value))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
for (let i = 0; i < 3; i++) {
  await ev('document.getElementById("bench-run").click()');
  for (let k = 0; k < 120; k++) { if ((await ev('document.getElementById("bench-out").textContent')).includes('modelLoadMs')) break; await sleep(500); }
  const b = JSON.parse(await ev('document.getElementById("bench-out").textContent'));
  console.log(JSON.stringify(b));
  await sleep(1000);
}
ws.close();
