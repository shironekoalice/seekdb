const B = 'http://127.0.0.1:8787';
const post = (p, b) => fetch(B + p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) }).then(async (r) => [r.status, await r.json()]);

function vec(seed, dim = 512) {
  const v = Array.from({ length: dim }, (_, j) => Math.sin(seed * 0.7 + j * 0.13) + Math.cos(seed * 1.3 + j * 0.07));
  const n = Math.hypot(...v);
  return v.map((x) => x / n);
}

console.log('create', await post('/api/collections', { name: 'notes' }));

const docs = [
  ['seekdb 简介', 'seekdb 是 AI 原生搜索数据库，支持向量与全文混合检索。', vec(1)],
  ['晚饭', '今天的晚饭是番茄炒蛋，做法很简单。', vec(2)],
  ['HNSW', 'HNSW 用分层图加速近邻搜索，建索引较慢。', vec(1.05)],
];
for (const [title, text, v] of docs) {
  const [s, d] = await post('/api/docs', { collection: 'notes', title, text, vector: v });
  console.log('add', s, d.id ? 'ok' : d);
}

for (const mode of ['semantic', 'keyword', 'hybrid']) {
  const t0 = performance.now();
  const [s, d] = await post('/api/search', { collection: 'notes', query: '混合检索', vector: vec(1.02), mode, k: 3 });
  console.log(mode, s, 'wall', Math.round(performance.now() - t0), 'ms db', d.dbMs, JSON.stringify(d.items?.map((i) => [i.title, i.score])));
}
const [s2, d2] = await post('/api/search', { collection: 'notes', query: '番茄', vector: vec(2.1), mode: 'hybrid', k: 3 });
console.log('hybrid 番茄', s2, JSON.stringify(d2.items?.map((i) => [i.title, i.score])), 'db', d2.dbMs);
