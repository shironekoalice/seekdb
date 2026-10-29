# mini-kb：浏览器端向量化 + seekdb 混合检索

按赛题 2.1–2.5 实现的迷你知识库：**embedding 全部在浏览器里算**（transformers.js / ONNX Runtime Web，
WebGPU 优先、WASM 自动降级），向量与原文写入 **seekdb（seekdb-js，嵌入式）**，
检索支持语义 / 关键词 / 混合（RRF 在库内一条 SQL 完成）。

![界面](docs/screenshot.png)

- 详细设计与实测数据：[docs/技术说明.md](docs/技术说明.md)
- 量化对比原始结果：[docs/bench-quality.json](docs/bench-quality.json)
- 端到端跑分与截图：[docs/e2e-result.json](docs/e2e-result.json)

## 跑起来

```bash
cd minikb
export PATH="$PWD/../.tools/node/bin:$PATH"      # 本机用仓库内 Node 22；已装 node>=20 可跳过
export SEEKDB_BINDINGS_CACHE_DIR="$PWD/.bindings" # 原生库缓存目录（默认 ~/.seekdb）
./run.sh
# 打开 http://127.0.0.1:8787
```

`run.sh` 会检查依赖，首次需 `npm install`（见下）。首次打开页面要下 24 MB int8 模型
（经本机代理 `/api/hf/*` 从 hf-mirror 拉取并缓存），等右上角出现「后端：WASM/int8」或
「后端：WebGPU/fp16」再操作。

依赖安装：

```bash
export PATH="$PWD/../.tools/node/bin:$PATH"
npm install --legacy-peer-deps --registry=https://registry.npmmirror.com seekdb express
npm install playwright-core   # 仅 e2e / 截图需要
```

> `--legacy-peer-deps` 是因为 seekdb 1.4.0 的 peer 依赖里带了 `workspace:*`，npm 会报 EUNSUPPORTEDPROTOCOL。

## 目录

```
minikb/
├── server.mjs              # Express + seekdb-js + HF 代理（约 300 行）
├── public/
│   ├── index.html / app.js / style.css
│   └── vendor/             # transformers.min.js + ORT wasm（本地 vendor，不走 CDN）
├── scripts/
│   ├── bench_quality.py    # 量化前后检索质量对比（离线标注集）
│   ├── e2e.mjs             # Playwright 端到端：载入示例 → 三种检索 → 性能面板 → 截图
│   └── test_api.mjs        # 纯 API 冒烟
├── docs/                   # 技术说明、截图、跑分 JSON
└── data/                   # 运行时：seekdb 数据 + 模型缓存（gitignore）
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 维度、集合与条数 |
| GET/POST | `/api/collections` | 列出 / 新建知识库 |
| DELETE | `/api/collections/:name` | 删除知识库 |
| GET/POST | `/api/docs?collection=` | 列表 / 写入（body 必须带 512 维 `vector`） |
| DELETE | `/api/docs/:id?collection=` | 删除文档 |
| POST | `/api/search` | `{collection, query, vector, mode, k}`，mode = semantic\|keyword\|hybrid |
| POST | `/api/refresh` | 手动刷索引（当前引擎下会超时，见技术说明 §3.3） |
| GET | `/api/hf/*` | 模型文件代理（磁盘缓存） |

## 与赛题要求的对应

| 要求 | 实现 |
|---|---|
| 2.1 文本文档导入、本地向量化、混合检索、展示原文 | 左侧添加/示例导入 + 右侧三种检索，结果带高亮原文 |
| 2.2.1 端侧运行时 | transformers.js 3.8.1 + ORT Web 1.22（wasm 本地 vendor） |
| 2.2.2 开源模型并注明来源 | Xenova/bge-small-zh-v1.5 ← BAAI/bge-small-zh-v1.5（MIT，512 维） |
| 2.2.3 INT8/FP16 量化与体积/质量报告 | int8 22.9 MB（3.95×，recall 持平，余弦一致性 0.9925）；fp16 45.3 MB（2.0×） |
| 2.2.4 WebGPU 优先 + 自动降级 + 界面提示 | `webgpu/fp16 → webgpu/fp32 → wasm/int8`，右上角徽章显示实际后端 |
| 2.3.1 seekdb-js 建集合存向量/原文/ID | `server.mjs` 里 `getOrCreateCollection` + `VECTOR(512)` |
| 2.3.2 混合检索由 seekdb 完成 | 单条 SQL 内两路召回 + RRF 融合（偏差说明见技术说明 §3.3） |
| 2.3.3 写入与索引刷新 | 精确扫描下写入即见；`refresh_index()` 在当前引擎会超时，已隔离出请求路径 |
| 2.3.4 增量写入/集合与会话状态 | 多知识库下拉切换，集合名随请求下发，localStorage 记住当前库 |
| 2.4 性能优化与实测 | 模型代理缓存、本地 vendor、批量 embedding、单次 SQL 融合；数据见技术说明 §4 |
| 2.5 现代浏览器 / 无云端推理 | Chromium 143 端到端跑通；服务端无任何推理调用 |

## 注意

- 只监听 `127.0.0.1`，无鉴权，别挂公网。
- `pylibseekdb` 是进程内嵌，别用多 worker 打开同一数据目录。
- 换 embedding 模型时，`public/app.js` 的 `DIM` 与 `server.mjs` 的 `KB_DIM` 要一起改，并重建 `data/`。
