/**
 * mini-kb 后端：Express + seekdb-js（嵌入式）。
 *
 * 职责边界（按赛题要求）：
 * - 不产生任何向量：embedding 全部由浏览器完成，这里只存/查。
 * - 混合检索调用 collection.hybridSearch()，由 seekdb 内部做向量+全文+RRF，
 *   服务端不做"两路结果自己拼"。
 * - 写入后调用 refresh_index()，保证新文档立即可检索。
 */

import express from 'express';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { SeekdbClient, Schema, VectorIndexConfig } from 'seekdb';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8787);
const DIM = Number(process.env.KB_DIM || 512);
const DATA_DIR = path.resolve(process.env.KB_DATA || path.join(__dirname, 'data'));
const DB_PATH = path.join(DATA_DIR, 'seekdb');
const HF_CACHE = path.join(DATA_DIR, 'hf_cache');
const HF_UPSTREAM = process.env.KB_HF_UPSTREAM || 'https://hf-mirror.com';

fs.mkdirSync(DB_PATH, { recursive: true });
fs.mkdirSync(HF_CACHE, { recursive: true });

// --------------------------------------------------------------------------- //
// seekdb
// --------------------------------------------------------------------------- //
const client = new SeekdbClient({ path: DB_PATH });

/** 串行化所有 DB 操作：嵌入式引擎是进程内的，不适合并发写。 */
let chain = Promise.resolve();
function serial(fn) {
  const run = chain.then(fn, fn);
  chain = run.then(() => {}, () => {});
  return run;
}

const cols = new Map();

async function collection(name) {
  if (cols.has(name)) return cols.get(name);
  const schema = new Schema({
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: DIM, distance: 'cosine' },
      embeddingFunction: null, // 向量由浏览器传入，服务端绝不 embedding
    }),
  });
  const col = await client.getOrCreateCollection({ name, schema, embeddingFunction: null });
  cols.set(name, col);
  return col;
}

const NAME_RE = /^[a-zA-Z0-9_]{1,40}$/;

// --------------------------------------------------------------------------- //
// 应用
// --------------------------------------------------------------------------- //
const app = express();
app.use(express.json({ limit: '8mb' }));

app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  next();
});

app.get('/api/health', async (req, res) => {
  try {
    const list = await serial(() => client.listCollections({ withEmbeddingFunction: false }));
    const items = [];
    for (const c of list) items.push({ name: c.name, count: await serial(() => c.count()) });
    res.json({ ok: true, dim: DIM, collections: items });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.get('/api/collections', async (req, res) => {
  try {
    const list = await serial(() => client.listCollections({ withEmbeddingFunction: false }));
    const items = [];
    for (const c of list) items.push({ name: c.name, count: await serial(() => c.count()) });
    res.json({ items });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/collections', async (req, res) => {
  const name = String(req.body?.name || '').trim();
  if (!NAME_RE.test(name)) {
    return res.status(400).json({ error: '集合名只能用字母、数字、下划线（1–40 字符）' });
  }
  try {
    await serial(() => collection(name));
    res.json({ ok: true, name });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.delete('/api/collections/:name', async (req, res) => {
  const name = req.params.name;
  try {
    cols.delete(name);
    await serial(() => client.deleteCollection(name));
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.get('/api/docs', async (req, res) => {
  const name = String(req.query.collection || 'notes');
  const limit = Math.min(Number(req.query.limit || 200), 500);
  try {
    const col = await serial(() => collection(name));
    const r = await serial(() => col.get({ limit, include: ['documents', 'metadatas'] }));
    const ids = r.ids || [];
    const docs = r.documents || [];
    const metas = r.metadatas || [];
    const items = ids.map((id, i) => ({
      id,
      title: metas[i]?.title || '',
      text: docs[i] || '',
      createdAt: metas[i]?.createdAt || '',
    }));
    items.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
    res.json({ items });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/docs', async (req, res) => {
  const { collection: name = 'notes', title = '', text = '', vector } = req.body || {};
  if (!text.trim()) return res.status(400).json({ error: 'text 不能为空' });
  if (!Array.isArray(vector) || vector.length !== DIM) {
    return res.status(400).json({ error: `向量维度应为 ${DIM}，收到 ${vector?.length ?? 0}` });
  }
  const id = crypto.randomUUID();
  const metadata = {
    title: String(title).trim() || text.trim().slice(0, 24),
    createdAt: new Date().toISOString(),
  };
  try {
    const col = await serial(() => collection(name));
    await serial(() => col.add({ ids: [id], documents: [text], embeddings: [vector], metadatas: [metadata] }));
    // 不在此处调 refresh_index()：当前嵌入式构建里它会阻塞数秒甚至超时；
    // 检索走库内精确扫描，写入即可见，无需等索引刷新（见 docs/技术说明.md）。
    res.json({ id, ...metadata });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

// 调试用：把指定文档连同库里存的向量一起取回来，证明确实写进了 seekdb
app.get('/api/docs/:id', async (req, res) => {
  const name = String(req.query.collection || 'notes');
  try {
    const col = await serial(() => collection(name));
    const r = await serial(() => col.get({ ids: [req.params.id], include: ['documents', 'metadatas', 'embeddings'] }));
    if (!r.ids?.length) return res.status(404).json({ error: 'not found' });
    const vec = r.embeddings?.[0] || [];
    res.json({
      id: r.ids[0],
      title: r.metadatas?.[0]?.title || '',
      text: r.documents?.[0] || '',
      dim: vec.length,
      vectorPreview: vec.slice(0, 4).map((x) => Number(x.toFixed(5))),
      vectorNorm: Number(Math.hypot(...vec).toFixed(4)),
    });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.delete('/api/docs/:id', async (req, res) => {
  const name = String(req.query.collection || 'notes');
  try {
    const col = await serial(() => collection(name));
    await serial(() => col.delete({ ids: [req.params.id] }));
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/search', async (req, res) => {
  const { collection: name = 'notes', query = '', vector, mode = 'hybrid', k = 8 } = req.body || {};
  if (!query.trim()) return res.status(400).json({ error: 'query 不能为空' });
  if (mode !== 'keyword' && (!Array.isArray(vector) || vector.length !== DIM)) {
    return res.status(400).json({ error: `向量维度应为 ${DIM}，收到 ${vector?.length ?? 0}` });
  }
  const n = Math.min(Math.max(Number(k) || 8, 1), 50);
  const RRF_K = 60;
  const POOL = 30; // 每路召回池大小

  try {
    const col = await serial(() => collection(name));
    const t0 = performance.now();
    let items = [];

    if (mode === 'semantic') {
      // 精确余弦扫描，排序与 top-k 都由 seekdb 完成
      const r = await serial(() => col.query({ queryEmbeddings: [vector], nResults: n, include: ['documents', 'metadatas', 'distances'], approximate: false }));
      items = (r.ids?.[0] ?? []).map((id, i) => {
        const dist = Number(r.distances?.[0]?.[i]);
        return {
          id,
          title: r.metadatas?.[0]?.[i]?.title || '',
          text: r.documents?.[0]?.[i] || '',
          score: 1 - dist,
          distance: dist,
          matchedBy: 'semantic',
        };
      });
    } else {
      const table = await serial(() => tableFor(name));
      const rows = mode === 'keyword'
        ? await serial(() => keywordSql(col, table, query, n))
        : await serial(() => hybridSql(col, table, query, vector, n, RRF_K, POOL));
      items = rows.map((r) => ({
        id: String(r._id),
        title: safeMeta(r.metadata)?.title || '',
        text: r.document || '',
        score: Number(r.score),
        distance: null,
        matchedBy: mode,
      }));
    }

    const dbMs = performance.now() - t0;
    res.json({ items, mode, k: n, dbMs: Number(dbMs.toFixed(2)) });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

/** 集合名 -> 物理表名（sdk_collections.collection_id） */
async function tableFor(name) {
  const rows = await client.execute(
    'SELECT collection_id FROM sdk_collections WHERE collection_name = ?',
    [name],
  );
  if (!rows?.length) throw new Error(`集合 ${name} 不存在`);
  return `c$v2$${rows[0].collection_id}`;
}

function safeMeta(m) {
  if (!m) return {};
  try {
    return typeof m === 'string' ? JSON.parse(m) : m;
  } catch {
    return {};
  }
}

function vectorHex(vec) {
  return Buffer.from(new Float32Array(vec).buffer).toString('hex').toUpperCase();
}

/**
 * 全文检索：单条 SQL，BM25 排序由 seekdb 完成。
 * 说明：这里没有用 collection.hybridSearch()，因为该 API 的向量分支依赖已构建的
 * HNSW 索引；当前嵌入式构建里 APPROXIMATE 查询恒返回空（见 docs/技术说明.md），
 * 所以改用库内 SQL 做融合，仍然只有一次数据库调用。
 */
async function keywordSql(col, table, query, n) {
  const sql = `
    SELECT _id, document, metadata, MATCH(document) AGAINST(?) AS score
    FROM \`${table}\`
    WHERE MATCH(document) AGAINST(?)
    ORDER BY score DESC
    LIMIT ?`;
  return client.execute(sql, [query, query, n]) ?? [];
}

/**
 * 混合检索：一条 SQL 内完成两路召回 + RRF 融合。
 * 客户端只发一次查询、拿一次结果，不做任何拼合。
 */
async function hybridSql(col, table, query, vector, n, rrfK, pool) {
  const hex = vectorHex(vector);
  const sql = `
    SELECT u._id AS _id, u.score AS score, t.document AS document, t.metadata AS metadata
    FROM (
      SELECT _id, SUM(1.0 / (? + rnk)) AS score
      FROM (
        SELECT * FROM (
          SELECT _id, ROW_NUMBER() OVER (ORDER BY MATCH(document) AGAINST(?) DESC) AS rnk
          FROM \`${table}\` WHERE MATCH(document) AGAINST(?)
        ) a WHERE rnk <= ?
        UNION ALL
        SELECT * FROM (
          SELECT _id, ROW_NUMBER() OVER (ORDER BY cosine_distance(embedding, X'${hex}')) AS rnk
          FROM \`${table}\`
        ) b WHERE rnk <= ?
      ) u0 GROUP BY _id
    ) u
    JOIN \`${table}\` t ON t._id = u._id
    ORDER BY u.score DESC
    LIMIT ?`;
  return client.execute(sql, [rrfK, query, query, pool, pool, n]) ?? [];
}

// 手动触发索引刷新（当前构建里可能超时，仅作为可选维护操作）
app.post('/api/refresh', async (req, res) => {
  const name = String(req.body?.collection || 'notes');
  try {
    const col = await serial(() => collection(name));
    const t0 = performance.now();
    await serial(() => Promise.race([
      col.refresh_index(),
      new Promise((_, rej) => setTimeout(() => rej(new Error('refresh 超时（8s）')), 8000)),
    ]));
    res.json({ ok: true, ms: Math.round(performance.now() - t0) });
  } catch (e) {
    res.json({ ok: false, error: String(e.message || e) });
  }
});

// --------------------------------------------------------------------------- //
// 模型文件代理（浏览器 -> 本机 -> hf-mirror），带磁盘缓存
// --------------------------------------------------------------------------- //
app.get(/^\/api\/hf\/(.*)$/, async (req, res) => {
  const rel = req.params[0];
  if (!rel || rel.includes('..')) return res.status(400).end();
  const key = crypto.createHash('sha256').update(rel).digest('hex');
  const body = path.join(HF_CACHE, key);
  const meta = `${body}.json`;

  try {
    if (fs.existsSync(body) && fs.existsSync(meta)) {
      const m = JSON.parse(await fsp.readFile(meta, 'utf8'));
      res.setHeader('Content-Type', m.contentType || 'application/octet-stream');
      return res.send(await fsp.readFile(body));
    }
    const upstream = await fetch(`${HF_UPSTREAM}/${rel}`, { redirect: 'follow' });
    if (!upstream.ok) return res.status(404).json({ error: `upstream ${upstream.status}` });
    const buf = Buffer.from(await upstream.arrayBuffer());
    const contentType = upstream.headers.get('content-type') || 'application/octet-stream';
    await fsp.writeFile(body, buf);
    await fsp.writeFile(meta, JSON.stringify({ contentType, url: `${HF_UPSTREAM}/${rel}` }));
    res.setHeader('Content-Type', contentType);
    res.send(buf);
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
});

app.use(express.static(path.join(__dirname, 'public')));

// 启动时确保有一个默认集合，前端打开就有地方可写
await serial(() => collection(process.env.KB_DEFAULT_COLLECTION || 'notes'));

app.listen(PORT, '127.0.0.1', () => {
  console.log(`→ http://127.0.0.1:${PORT}  (dim=${DIM}, db=${DB_PATH})`);
});
