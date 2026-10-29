import { pipeline, env } from '/vendor/transformers.min.js';

// --------------------------------------------------------------------------- //
// 配置
// --------------------------------------------------------------------------- //
const MODEL_ID = 'Xenova/bge-small-zh-v1.5'; // BAAI/bge-small-zh-v1.5 的 ONNX 转换
const DIM = 512;
const BACKEND_LABEL = { webgpu: 'WebGPU', wasm: 'WASM' };
const DTYPE_LABEL = { fp16: 'fp16', q8: 'int8' };

env.allowLocalModels = false;
env.remoteHost = `${location.origin}/api/hf`; // 本机反代，避免直连 HF 的墙与 CORS

const ortEnv = env?.backends?.onnx;
if (ortEnv?.wasm) {
  ortEnv.wasm.wasmPaths = `${location.origin}/vendor/ort/`; // 默认会去 jsdelivr，国内常挂
  ortEnv.wasm.numThreads = 1; // 免 SharedArrayBuffer/COOP 要求
}

const $ = (id) => document.getElementById(id);
const els = {
  backend: $('backend-badge'), model: $('model-badge'), db: $('db-badge'),
  collection: $('collection'), newCollection: $('new-collection'),
  kbNew: $('kb-new'), kbName: $('kb-name'), kbCreate: $('kb-create'), kbCancel: $('kb-cancel'),
  title: $('title'), text: $('text'), add: $('add'), seed: $('seed'), addHint: $('add-hint'),
  docs: $('docs'), docsEmpty: $('docs-empty'), count: $('count'),
  q: $('q'), search: $('search'), modes: $('modes'), modeHint: $('mode-hint'), searchMeta: $('search-meta'),
  results: $('results'), resultsEmpty: $('results-empty'),
  benchRun: $('bench-run'), benchOut: $('bench-out'),
};

const state = {
  extractor: null,
  backend: null, // 'webgpu' | 'wasm'
  dtype: null,
  modelLoadMs: null,
  collection: localStorage.getItem('kb.collection') || 'notes',
  mode: 'hybrid',
  busy: false,
};

// --------------------------------------------------------------------------- //
// 状态
// --------------------------------------------------------------------------- //
function badge(el, text, kind = '') {
  el.textContent = text;
  el.className = 'badge' + (kind ? ` ${kind}` : '');
}

function updateControls() {
  const ready = !!state.extractor;
  els.add.disabled = !ready || state.busy;
  els.seed.disabled = !ready || state.busy;
  els.benchRun.disabled = !ready || state.busy;
  els.search.disabled = state.busy || !(ready || state.mode === 'keyword');
}

function setBusy(v) {
  state.busy = v;
  updateControls();
}

function errorText(e) {
  return String(e?.message || e);
}

// --------------------------------------------------------------------------- //
// 模型：优先 WebGPU/fp16，失败自动降级 WASM/int8
// --------------------------------------------------------------------------- //
async function loadModel() {
  const attempts = [];
  const forced = new URLSearchParams(location.search).get('backend'); // ?backend=wasm 可强制降级（调试用）
  if (navigator.gpu && forced !== 'wasm') {
    attempts.push({ device: 'webgpu', dtype: 'fp16' }); // 首选：GPU + 半精度
    attempts.push({ device: 'webgpu', dtype: 'fp32' }); // 设备不支持 fp16 时退一步
  }
  attempts.push({ device: 'wasm', dtype: 'q8' }); // 兜底：CPU + INT8

  const tried = [];
  for (const cfg of attempts) {
    const label = `${BACKEND_LABEL[cfg.device]}/${DTYPE_LABEL[cfg.dtype]}`;
    badge(els.backend, `后端：${label} 加载中…`);
    try {
      const t0 = performance.now();
      const pipe = await pipeline('feature-extraction', MODEL_ID, {
        ...cfg,
        progress_callback: (p) => {
          if (p.status === 'progress' && p.total) {
            const pct = Math.min(100, Math.round(((p.loaded || 0) / p.total) * 100));
            badge(els.backend, `后端：${label} 下载 ${pct}%`);
          }
        },
      });
      // 真跑一次，确认这个后端在这台机器上确实能推理
      await pipe(['预热'], { pooling: 'cls', normalize: true });
      const loadMs = Math.round(performance.now() - t0);

      state.extractor = pipe;
      state.backend = cfg.device;
      state.dtype = cfg.dtype;
      state.modelLoadMs = loadMs;
      badge(els.backend, `后端：${label}`, 'ok');
      badge(els.model, `模型：bge-small-zh-v1.5 · ${DIM} 维`);
      return;
    } catch (e) {
      console.warn(`[kb] ${label} 不可用：`, e);
      tried.push(`${label}: ${errorText(e).slice(0, 80)}`);
    }
  }
  badge(els.backend, '后端：不可用', 'err');
  throw new Error(`推理后端加载失败\n${tried.join('\n')}`);
}

// --------------------------------------------------------------------------- //
// embedding
// --------------------------------------------------------------------------- //
async function embed(texts) {
  const out = await state.extractor(texts, { pooling: 'cls', normalize: true });
  return out.tolist();
}

async function embedOne(text) {
  const [v] = await embed([text]);
  return v;
}

// --------------------------------------------------------------------------- //
// 知识库（集合）
// --------------------------------------------------------------------------- //
async function loadCollections(select) {
  const r = await fetch('/api/collections').then((x) => x.json());
  const items = r.items || [];
  els.collection.innerHTML = '';
  for (const it of items) {
    const opt = document.createElement('option');
    opt.value = it.name;
    opt.textContent = `${it.name} (${it.count})`;
    els.collection.appendChild(opt);
  }
  if (select && items.some((i) => i.name === select)) {
    els.collection.value = select;
  } else if (items.length) {
    els.collection.value = items[0].name;
  }
  state.collection = els.collection.value;
  localStorage.setItem('kb.collection', state.collection);
}

async function createCollection() {
  const name = els.kbName.value.trim();
  if (!name) return;
  const r = await fetch('/api/collections', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const data = await r.json();
  if (!r.ok) {
    els.addHint.textContent = data.error || '创建失败';
    els.addHint.classList.add('warn');
    return;
  }
  els.kbNew.hidden = true;
  els.kbName.value = '';
  await switchCollection(name);
}

async function switchCollection(name) {
  state.collection = name;
  localStorage.setItem('kb.collection', name);
  els.searchMeta.textContent = '';
  els.results.innerHTML = '';
  els.resultsEmpty.hidden = false;
  await refresh();
}

// --------------------------------------------------------------------------- //
// 文档列表
// --------------------------------------------------------------------------- //
async function refresh() {
  const [health, docs] = await Promise.all([
    fetch('/api/health').then((r) => r.json()),
    fetch(`/api/docs?collection=${encodeURIComponent(state.collection)}`).then((r) => r.json()),
  ]);
  const cur = (health.collections || []).find((c) => c.name === state.collection);
  els.count.textContent = cur?.count ?? docs.items?.length ?? 0;
  // 同步下拉框里的条数，避免写入后仍显示 0
  for (const opt of els.collection.options) {
    const c = (health.collections || []).find((x) => x.name === opt.value);
    if (c) opt.textContent = `${c.name} (${c.count})`;
  }
  badge(els.db, `seekdb：${health.dim} 维 · ${state.collection}`, 'ok');
  renderDocs(docs.items || []);
}

function renderDocs(items) {
  els.docs.innerHTML = '';
  els.docsEmpty.hidden = items.length > 0;
  for (const d of items) {
    const li = document.createElement('li');

    const t = document.createElement('div');
    t.className = 't';
    t.textContent = d.title || '（无标题）';

    const x = document.createElement('div');
    x.className = 'x';
    x.textContent = (d.text || '').slice(0, 80);

    const del = document.createElement('button');
    del.className = 'del';
    del.title = '删除';
    del.textContent = '×';
    del.onclick = async () => {
      await fetch(`/api/docs/${d.id}?collection=${encodeURIComponent(state.collection)}`, { method: 'DELETE' });
      await refresh();
    };

    li.append(t, x, del);
    els.docs.appendChild(li);
  }
}

// --------------------------------------------------------------------------- //
// 写入
// --------------------------------------------------------------------------- //
async function addDoc() {
  const text = els.text.value.trim();
  if (!text || !state.extractor) return;
  setBusy(true);
  els.addHint.classList.remove('warn');
  els.addHint.textContent = '本地向量化中…';
  try {
    const t0 = performance.now();
    const vector = await embedOne(text);
    const embedMs = Math.round(performance.now() - t0);
    const r = await fetch('/api/docs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ collection: state.collection, title: els.title.value.trim(), text, vector }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '写入失败');
    els.title.value = '';
    els.text.value = '';
    els.addHint.textContent = `已入库：${DIM} 维，embedding ${embedMs} ms（${BACKEND_LABEL[state.backend]}）`;
    await refresh();
  } catch (e) {
    els.addHint.textContent = errorText(e);
    els.addHint.classList.add('warn');
  } finally {
    setBusy(false);
  }
}

const SEED = [
  ['seekdb 是什么', 'seekdb 是 OceanBase 开源的 AI 原生搜索数据库，把关系、向量、全文、JSON、GIS 放在同一个引擎里，支持单条 SQL 完成向量与全文的混合检索。它既可以嵌入式运行，也可以作为独立服务部署。'],
  ['混合检索如何排序', 'seekdb 的混合检索支持多路召回：向量检索负责语义相似，全文检索（IK 分词 + BM25）负责关键词命中。融合阶段可以用加权分数、RRF（倒数排名融合）或 LLM 重排，常用的是 RRF。'],
  ['HNSW 索引', 'HNSW 是分层可导航小世界图，通过多层跳表式的图结构把近邻搜索的复杂度压到对数级。建索引时逐点插入并连边，构建成本高，但查询延迟低、召回好。'],
  ['浏览器端向量化', 'transformers.js 让 ONNX 模型直接跑在浏览器里，用 WebAssembly 或 WebGPU 推理。好处是文本不出本机、没有 embedding API 费用，代价是首次要下载模型，速度受客户端算力限制。'],
  ['hipVS 是什么', 'hipVS 是 AMD ROCm 生态里的 GPU 加速向量检索库，属于 ROCm-DS 数据科学工具包，API 与 NVIDIA cuVS 对齐。它用 GPU 并行建图来加速 HNSW / CAGRA 索引构建。'],
  ['RAG 的基本流程', 'RAG 先从知识库里检索出与问题最相关的片段，再把这些片段塞进提示词交给大模型作答。检索质量直接决定回答质量，所以混合检索和重排往往是效果的关键。'],
];

async function seedDocs() {
  if (!state.extractor) return;
  setBusy(true);
  try {
    for (let i = 0; i < SEED.length; i++) {
      const [title, text] = SEED[i];
      els.addHint.textContent = `向量化示例 ${i + 1}/${SEED.length}…`;
      const [vector] = await embed([text]);
      const r = await fetch('/api/docs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ collection: state.collection, title, text, vector }),
      });
      if (!r.ok) throw new Error((await r.json()).error || '写入失败');
    }
    els.addHint.textContent = '示例已写入当前知识库。';
    await refresh();
  } catch (e) {
    els.addHint.textContent = errorText(e);
    els.addHint.classList.add('warn');
  } finally {
    setBusy(false);
  }
}

// --------------------------------------------------------------------------- //
// 检索
// --------------------------------------------------------------------------- //
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function highlight(text, query) {
  let html = escapeHtml(text);
  const tokens = new Set([query, ...query.split(/[\s,，。？?、]+/)].map((t) => t.trim()).filter((t) => t.length >= 2));
  for (const tok of tokens) {
    const esc = escapeHtml(tok);
    html = html.split(esc).join(`<mark>${esc}</mark>`);
  }
  return html;
}

function renderResults(items, query) {
  els.results.innerHTML = '';
  els.resultsEmpty.hidden = items.length > 0;
  if (!items.length) {
    els.resultsEmpty.textContent = '没有命中。换个说法，或切到关键词模式。';
    return;
  }
  for (const it of items) {
    const card = document.createElement('div');
    card.className = 'card';

    const head = document.createElement('div');
    head.className = 'head';

    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = it.title || '（无标题）';

    const score = document.createElement('div');
    score.className = 'score';
    score.textContent = it.score == null
      ? ''
      : it.matchedBy === 'semantic'
        ? `相似度 ${(it.score * 100).toFixed(1)}%`
        : `相关度 ${Number(it.score).toFixed(3)}`;

    const body = document.createElement('div');
    body.className = 'body';
    body.innerHTML = highlight(it.text, query);

    head.append(title, score);
    card.append(head, body);
    els.results.appendChild(card);
  }
}

async function doSearch() {
  const query = els.q.value.trim();
  if (!query) return;
  setBusy(true);
  els.results.innerHTML = '';
  els.resultsEmpty.hidden = true;
  els.searchMeta.textContent = '';
  try {
    let vector;
    let embedMs = 0;
    if (state.mode !== 'keyword') {
      const t0 = performance.now();
      vector = await embedOne(query);
      embedMs = Math.round(performance.now() - t0);
    }
    const r = await fetch('/api/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ collection: state.collection, query, vector, mode: state.mode, k: 8 }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || '检索失败');
    renderResults(data.items, query);
    els.searchMeta.textContent =
      `query embedding ${embedMs} ms · seekdb ${data.dbMs} ms · ${data.items.length} 条结果`;
  } catch (e) {
    els.searchMeta.textContent = errorText(e);
  } finally {
    setBusy(false);
  }
}

// --------------------------------------------------------------------------- //
// 性能面板
// --------------------------------------------------------------------------- //
async function runBench() {
  if (!state.extractor) return;
  els.benchRun.disabled = true;
  els.benchOut.textContent = '测量中…';
  try {
    const sample = 'seekdb 把向量与全文检索放在一个引擎里，用 RRF 融合两路召回结果。';
    const batch = Array.from({ length: 8 }, (_, i) => `${sample} #${i + 1}`);

    // 稳态延迟：先热身一次
    await embed([sample]);

    const t1 = [];
    for (let i = 0; i < 5; i++) {
      const t0 = performance.now();
      await embed([sample]);
      t1.push(performance.now() - t0);
    }
    const t8 = [];
    for (let i = 0; i < 3; i++) {
      const t0 = performance.now();
      await embed(batch);
      t8.push(performance.now() - t0);
    }

    const dbTimes = [];
    const qv = await embedOne(sample);
    for (let i = 0; i < 3; i++) {
      const r = await fetch('/api/search', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ collection: state.collection, query: '混合检索', vector: qv, mode: 'hybrid', k: 5 }),
      });
      const d = await r.json();
      if (r.ok) dbTimes.push(d.dbMs);
    }

    const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
    const result = {
      backend: `${BACKEND_LABEL[state.backend]}/${DTYPE_LABEL[state.dtype]}`,
      modelLoadMs: state.modelLoadMs,
      embedSingleMs: Number(avg(t1).toFixed(1)),
      batch8TotalMs: Number(avg(t8).toFixed(1)),
      batch8PerItemMs: Number((avg(t8) / 8).toFixed(1)),
      dbSearchMs: dbTimes.map((x) => Number(x.toFixed(2))),
      docs: els.count.textContent,
    };
    els.benchOut.textContent = JSON.stringify(result, null, 2);
    localStorage.setItem('kb.lastBench', JSON.stringify(result));
  } catch (e) {
    els.benchOut.textContent = `测量失败：${errorText(e)}`;
  } finally {
    els.benchRun.disabled = false;
  }
}

// --------------------------------------------------------------------------- //
// 事件与启动
// --------------------------------------------------------------------------- //
els.add.onclick = addDoc;
els.seed.onclick = seedDocs;
els.search.onclick = doSearch;
els.q.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
els.benchRun.onclick = runBench;

els.collection.onchange = () => switchCollection(els.collection.value);
els.newCollection.onclick = () => { els.kbNew.hidden = !els.kbNew.hidden; els.kbName.focus(); };
els.kbCancel.onclick = () => { els.kbNew.hidden = true; };
els.kbCreate.onclick = createCollection;
els.kbName.addEventListener('keydown', (e) => { if (e.key === 'Enter') createCollection(); });

els.modes.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-mode]');
  if (!btn) return;
  state.mode = btn.dataset.mode;
  [...els.modes.children].forEach((b) => b.classList.toggle('active', b === btn));
  els.modeHint.textContent = {
    hybrid: '混合 = 向量召回 + 全文召回，RRF 融合在 seekdb 内部完成。',
    semantic: '语义 = 只比向量距离，能命中同义、近义的说法。',
    keyword: '关键词 = 全文索引（IK 分词 + BM25），适合精确术语。',
  }[state.mode];
  updateControls();
});

(async function init() {
  updateControls();
  try {
    await loadCollections(state.collection);
    await refresh();
  } catch (e) {
    badge(els.db, `seekdb：${errorText(e).slice(0, 40)}`, 'err');
  }

  try {
    await loadModel();
  } catch (e) {
    badge(els.backend, '后端：不可用', 'err');
    els.addHint.textContent = `模型加载失败：${errorText(e).slice(0, 200)}`;
    els.addHint.classList.add('warn');
  }
  updateControls();
})();
