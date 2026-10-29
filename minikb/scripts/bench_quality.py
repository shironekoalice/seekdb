"""量化前后检索质量对比（离线评测，非应用路径）。

用同一份 ONNX 图（fp32 vs int8）在这台机器的 CPU 上跑 embedding，
在一套人工标注的中文小语料上直接比 recall@k 和向量一致性。

注意：这是离线实验脚本，应用本身（浏览器 + seekdb）不调用它，也不在服务端做 embedding。

用法（在 kb/ 的 venv 里跑，那里有 onnxruntime + tokenizers）：
    ../kb/.venv/bin/python scripts/bench_quality.py
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
HF = "https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main"

VARIANTS = {
    "fp32": ("onnx/model.onnx", "model.onnx"),
    "int8": ("onnx/model_quantized.onnx", "model_quantized.onnx"),
}

# --------------------------------------------------------------------------- #
# 评测集：40 篇文档 + 12 条人工标注查询（措辞与原文不同）
# --------------------------------------------------------------------------- #
_set = json.loads((ROOT / "tests" / "quality_set.json").read_text())
DOCS = _set["docs"]
QUERIES = [(q["query"], q["expected_doc_index"]) for q in _set["queries"]]


def ensure(url: str, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[下载] {url}")
    subprocess.run(["curl", "-sSL", "-m", "900", url, "-o", str(dest)], check=True)


def load_tokenizer() -> Tokenizer:
    path = MODELS / "tokenizer.json"
    ensure(f"{HF}/tokenizer.json", path)
    return Tokenizer.from_file(str(path))


def encode(tok: Tokenizer, texts: list[str], max_len: int = 128):
    encs = tok.encode_batch(texts)
    ids = np.zeros((len(texts), max_len), dtype=np.int64)
    mask = np.zeros((len(texts), max_len), dtype=np.int64)
    for i, e in enumerate(encs):
        n = min(len(e.ids), max_len)
        ids[i, :n] = e.ids[:n]
        mask[i, :n] = 1
    return ids, mask


def embed(sess, tok, texts, batch=8):
    out = []
    for s in range(0, len(texts), batch):
        chunk = texts[s:s + batch]
        ids, mask = encode(tok, chunk)
        feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        hidden = sess.run(None, feed)[0]
        cls = hidden[:, 0, :]  # CLS pooling，和前端保持一致
        cls = cls / np.linalg.norm(cls, axis=1, keepdims=True)
        out.append(cls.astype(np.float32))
    return np.vstack(out)


def recall_at(emb_docs, emb_q, k):
    sims = emb_q @ emb_docs.T
    top = np.argsort(-sims, axis=1)[:, :k]
    hits1 = sum(int(top[i, 0] == QUERIES[i][1]) for i in range(len(QUERIES)))
    hitsk = sum(int(QUERIES[i][1] in top[i].tolist()) for i in range(len(QUERIES)))
    return hits1 / len(QUERIES), hitsk / len(QUERIES), top, sims


def main():
    tok = load_tokenizer()
    doc_texts = [d for d in DOCS]
    q_texts = [q for q, _ in QUERIES]

    results = {}
    emb = {}
    for name, (rel, local) in VARIANTS.items():
        path = MODELS / local
        ensure(f"{HF}/{rel}", path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        t0 = time.perf_counter()
        dvec = embed(sess, tok, doc_texts)
        dt = time.perf_counter() - t0
        qvec = embed(sess, tok, q_texts)
        r1, r3, top, sims = recall_at(dvec, qvec, 3)
        emb[name] = dvec
        results[name] = {
            "size_mb": round(path.stat().st_size / 2**20, 1),
            "recall@1": round(r1, 3),
            "recall@3": round(r3, 3),
            "embed_ms_per_doc": round(dt / len(doc_texts) * 1000, 1),
            "misses": [QUERIES[i][0] for i in range(len(QUERIES)) if QUERIES[i][1] not in top[i].tolist()],
        }
        if name == "fp32":
            base_sims = sims

    # 与 fp32 的一致性
    if "fp32" in emb and "int8" in emb:
        cos = np.sum(emb["fp32"] * emb["int8"], axis=1)
        results["int8"]["cosine_vs_fp32_mean"] = round(float(cos.mean()), 4)
        results["int8"]["cosine_vs_fp32_min"] = round(float(cos.min()), 4)

    results["fp32"]["compression"] = 1.0
    if "int8" in results:
        results["int8"]["compression"] = round(results["fp32"]["size_mb"] / results["int8"]["size_mb"], 2)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "bench-quality.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
