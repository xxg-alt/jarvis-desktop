# -*- coding: utf-8 -*-
"""
贾维斯本地 RAG 引擎（检索增强生成）
- 向量模型: bge-small-zh-v1.5 (ONNX, CPU, 512 维), 放在 models/bge-small-zh-v1.5/
- 向量库: %APPDATA%/Jarvis/rag_index.json, 分 docs(文档片段) 和 mem(记忆条目) 两区
- 检索: 归一化向量点积, 不依赖任何外部向量数据库
- 增量: 文档按 mtime+size, 记忆按内容 hash, 变了才重算向量
- 原则: 一切失败静默降级(返回 None/空), 绝不让贾维斯主流程崩掉
"""
import base64
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
MODEL_DIR = BASE / "models" / "bge-small-zh-v1.5"
_INDEX_ENV = os.environ.get("JARVIS_RAG_INDEX", "").strip()
INDEX_PATH = Path(_INDEX_ENV) if _INDEX_ENV else (
    Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis" / "rag_index.json"
)

# 默认扫描根与文件工具共用 allowed_roots() 这一处白名单; 测试可用 JARVIS_DOC_DIRS 覆盖 (分号分隔)
_ENV_DIRS = os.environ.get("JARVIS_DOC_DIRS", "").strip()
DOC_DIRS = []


def doc_dirs() -> list:
    """RAG 扫描根：默认从文件工具白名单现取，不在这里另列一套目录。"""
    if _ENV_DIRS:
        roots = [Path(p) for p in _ENV_DIRS.split(";") if p.strip()]
    else:
        try:
            from system_tools import allowed_roots
            roots = [Path(p) for p in allowed_roots()]
        except Exception as exc:
            print(f"[RAG] 授权目录读取失败: {exc}")
            roots = []
    DOC_DIRS[:] = roots
    return DOC_DIRS


DOC_EXTS = {".txt", ".md", ".pdf", ".docx"}
MAX_FILE_MB = 10
MAX_CHUNKS = 4000        # 全库片段上限, 防索引无限膨胀
MAX_FILE_CHUNKS = 80     # 单文件片段上限
MAX_DEPTH = 3            # 目录递归深度
CHUNK_SIZE = 300         # 每块字符数
CHUNK_OVERLAP = 60
DOC_MIN_SCORE = 0.42     # 低于此分数视为"没翻到" (实测无关问题约 0.37, 相关约 0.45+)
MEM_MIN_SCORE = 0.42     # 记忆同理: 噪声约 0.39 封顶, 正确召回 0.47+

# 桌面上有整个项目文件夹, 扫文档必须绕开, 否则会把 venv/dist 全编进索引
EXCLUDE_DIR_NAMES = {
    ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".git", ".idea", ".vscode", "$RECYCLE.BIN", "System Volume Information",
}

# bge 中文系列: 查询侧加检索指令前缀, 文档侧不加
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# ---------- 向量模型 (懒加载单例) ----------
_model_lock = threading.Lock()
_session = None
_tokenizer = None
_model_failed = False


def _model_ready() -> bool:
    global _session, _tokenizer, _model_failed
    if _model_failed:
        return False
    if _session is not None:
        return True
    with _model_lock:
        if _session is not None:
            return True
        if _model_failed:
            return False
        try:
            if not (MODEL_DIR / "model.onnx").exists() or not (MODEL_DIR / "tokenizer.json").exists():
                print("[RAG] 找不到向量模型 models/bge-small-zh-v1.5, 语义检索停用")
                _model_failed = True
                return False
            import onnxruntime as ort
            from tokenizers import Tokenizer
            _session = ort.InferenceSession(
                str(MODEL_DIR / "model.onnx"), providers=["CPUExecutionProvider"]
            )
            tk = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
            tk.enable_truncation(max_length=256)
            _tokenizer = tk
            return True
        except Exception as exc:
            print(f"[RAG] 向量模型加载失败, 语义检索停用: {exc}")
            _model_failed = True
            return False


def _embed(texts, is_query=False):
    """批量把文本变成 512 维归一化向量; 模型不可用返回 None。"""
    if not texts or not _model_ready():
        return None
    if is_query:
        texts = [_QUERY_PREFIX + t for t in texts]
    try:
        with _model_lock:
            encs = _tokenizer.encode_batch(list(texts))
            maxlen = max(len(e.ids) for e in encs)
            ids = np.zeros((len(encs), maxlen), dtype=np.int64)
            mask = np.zeros((len(encs), maxlen), dtype=np.int64)
            typ = np.zeros((len(encs), maxlen), dtype=np.int64)
            for i, e in enumerate(encs):
                ids[i, : len(e.ids)] = e.ids
                mask[i, : len(e.attention_mask)] = e.attention_mask
                typ[i, : len(e.type_ids)] = e.type_ids
            hidden = _session.run(
                None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": typ}
            )[0]
        m = mask[:, :, None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
    except Exception as exc:
        print(f"[RAG] 向量计算失败: {exc}")
        return None


# ---------- 向量库 (docs + mem 两区) ----------
_io_lock = threading.Lock()
_docs = []          # [{"path","mtime","size","text","vec":np.ndarray}]
_mem = []           # [{"key","kind","text","item","vec":np.ndarray}]
_loaded = False


def _enc_vec(v) -> str:
    return base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii")


def _dec_vec(s) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)


def _load_index():
    global _docs, _mem, _loaded
    if _loaded:
        return
    with _io_lock:
        if _loaded:
            return
        _loaded = True
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            print(f"[RAG] 索引读取失败, 从空索引开始: {exc}")
            return
        docs, mem = [], []
        if isinstance(data, dict):
            for e in data.get("docs", []):
                try:
                    docs.append({
                        "path": str(e["path"]), "mtime": float(e["mtime"]),
                        "size": int(e["size"]), "text": str(e["text"]),
                        "vec": _dec_vec(e["vec"]),
                    })
                except Exception:
                    continue
            for e in data.get("mem", []):
                try:
                    mem.append({
                        "key": str(e["key"]), "kind": str(e["kind"]),
                        "text": str(e["text"]), "item": e.get("item") or {},
                        "vec": _dec_vec(e["vec"]),
                    })
                except Exception:
                    continue
        _docs, _mem = docs, mem


def _save_index_locked():
    try:
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "docs": [
                {"path": e["path"], "mtime": e["mtime"], "size": e["size"],
                 "text": e["text"], "vec": _enc_vec(e["vec"])}
                for e in _docs
            ],
            "mem": [
                {"key": e["key"], "kind": e["kind"], "text": e["text"],
                 "item": e["item"], "vec": _enc_vec(e["vec"])}
                for e in _mem
            ],
        }
        temp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, INDEX_PATH)
    except Exception as exc:
        print(f"[RAG] 索引保存失败: {exc}")


def _hash_text(t) -> str:
    return hashlib.md5(str(t).encode("utf-8", "ignore")).hexdigest()[:16]


# ---------- 文档解析与切块 ----------
def _parse_file(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            return ""
        suffix = path.suffix.lower()
        if suffix in (".txt", ".md"):
            raw = path.read_bytes()
            for enc in ("utf-8", "gbk"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages = []
            for i, page in enumerate(reader.pages):
                if i >= 200:
                    break
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        if suffix == ".docx":
            import docx
            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
            for table in d.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
    except Exception as exc:
        print(f"[RAG] 解析失败 {path.name}: {exc}")
    return ""


def _chunks(text: str):
    """按段落聚合, 超长段落滑窗; 返回不超过 MAX_FILE_CHUNKS 块。"""
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, buf = [], ""
    for para in paras:
        if len(buf) + len(para) + 1 <= CHUNK_SIZE:
            buf = (buf + "\n" + para).strip()
            continue
        if buf:
            chunks.append(buf)
        while len(para) > CHUNK_SIZE:
            chunks.append(para[:CHUNK_SIZE])
            para = para[CHUNK_SIZE - CHUNK_OVERLAP:]
        buf = para
    if buf:
        chunks.append(buf)
    return chunks[:MAX_FILE_CHUNKS]


def _iter_doc_files():
    for root in doc_dirs():
        if not root.exists():
            continue
        stack = [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for p in entries:
                try:
                    if p.is_dir():
                        if p.name in EXCLUDE_DIR_NAMES or p.name.startswith("."):
                            continue
                        if p == BASE or BASE in p.parents:  # 跳过项目目录自身
                            continue
                        if depth < MAX_DEPTH:
                            stack.append((p, depth + 1))
                    elif p.suffix.lower() in DOC_EXTS and not p.name.startswith("~$"):
                        yield p
                except OSError:
                    continue


# ---------- 对外接口 ----------
def index_docs():
    """增量重建文档向量。返回统计 dict; 模型不可用返回 None。"""
    if not _model_ready():
        return None
    _load_index()
    with _io_lock:
        by_path = {}
        for e in _docs:
            by_path.setdefault(e["path"], []).append(e)

    stats = {"scanned": 0, "updated": 0, "removed": 0, "chunks": 0, "skipped": 0}
    seen, new_docs = set(), []
    for path in _iter_doc_files():
        stats["scanned"] += 1
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        seen.add(key)
        old = by_path.get(key)
        if old and old[0].get("size", -1) == st.st_size and abs(
            old[0].get("mtime", 0) - st.st_mtime
        ) < 1:
            new_docs.extend(old)  # 没变, 直接保留旧向量
            continue
        if len(new_docs) >= MAX_CHUNKS:
            stats["skipped"] += 1
            continue
        chunks = _chunks(_parse_file(path))
        if not chunks:
            continue
        vecs = _embed(chunks)
        if vecs is None:
            continue
        stats["updated"] += 1
        for ch, v in zip(chunks, vecs):
            new_docs.append({
                "path": key, "mtime": st.st_mtime, "size": st.st_size,
                "text": ch, "vec": v,
            })
    stats["removed"] = sum(1 for p in by_path if p not in seen)

    with _io_lock:
        _docs[:] = new_docs
        _save_index_locked()
    stats["chunks"] = len(new_docs)
    return stats


def search_docs(query: str, top_k: int = 3):
    """在文档库里语义检索; 返回 [{"path","text","score"}], 低于阈值不返回。
    模型不可用返回 None (调用方据此提示"知识库没准备好")。"""
    if not _model_ready():
        return None
    _load_index()
    with _io_lock:
        entries = list(_docs)
    if not entries:
        return []
    q = _embed([query], is_query=True)
    if q is None:
        return None
    mat = np.stack([e["vec"] for e in entries])
    scores = mat @ q[0]
    order = np.argsort(-scores)[:top_k]
    return [
        {"path": entries[i]["path"], "text": entries[i]["text"], "score": float(scores[i])}
        for i in order
        if scores[i] >= DOC_MIN_SCORE
    ]


def sync_memory(entries):
    """把记忆条目增量编入 mem 区。entries: [(kind, 检索文本, 原始item)]。
    已消失的条目 (含清空记忆) 会自动删除。返回 True 表示语义检索可用。"""
    if not _model_ready():
        return False
    _load_index()
    want = {}
    for kind, text, item in entries:
        text = str(text or "").strip()
        if text:
            want[f"{kind}:{_hash_text(text)}"] = (kind, text, item)
    with _io_lock:
        old = {e["key"]: e for e in _mem}

    new_keys = [k for k in want if k not in old]
    if new_keys:
        vecs = _embed([want[k][1] for k in new_keys])
        if vecs is None:
            return False
        with _io_lock:
            for k, v in zip(new_keys, vecs):
                kind, text, item = want[k]
                old[k] = {"key": k, "kind": kind, "text": text, "item": item, "vec": v}
            _mem[:] = [e for k, e in old.items() if k in want]
            _save_index_locked()
    else:
        with _io_lock:
            alive = [e for e in _mem if e["key"] in want]
            if len(alive) != len(_mem):
                _mem[:] = alive
                _save_index_locked()
    return True


def search_mem(query: str, kinds=None, top_k: int = 3):
    """在记忆库里语义检索; 返回 [{"kind","score","item"}], 低分过滤。
    模型不可用返回 None。"""
    if not _model_ready():
        return None
    _load_index()
    with _io_lock:
        entries = [e for e in _mem if kinds is None or e["kind"] in kinds]
    if not entries:
        return []
    q = _embed([query], is_query=True)
    if q is None:
        return None
    mat = np.stack([e["vec"] for e in entries])
    scores = mat @ q[0]
    order = np.argsort(-scores)[:top_k]
    return [
        {"kind": entries[i]["kind"], "score": float(scores[i]), "item": entries[i]["item"]}
        for i in order
        if scores[i] >= MEM_MIN_SCORE
    ]


def clear_mem():
    """清空记忆向量 (文档向量保留)。"""
    _load_index()
    with _io_lock:
        if _mem:
            _mem[:] = []
            _save_index_locked()


_started = False
_startup_lock = threading.Lock()


def startup():
    """后台预热: 加载模型 + 增量建文档索引。幂等, 不阻塞主流程。"""
    global _started
    with _startup_lock:
        if _started:
            return
        _started = True

    def _bg():
        try:
            if not _model_ready():
                return
            stats = index_docs()
            if stats:
                print(
                    f"[RAG] 文档索引完成: 扫描{stats['scanned']}个文件, "
                    f"更新{stats['updated']}, 移除{stats['removed']}, 共{stats['chunks']}个片段"
                )
        except Exception as exc:
            print(f"[RAG] 后台索引失败: {exc}")

    threading.Thread(target=_bg, name="jarvis-rag-index", daemon=True).start()
