# -*- coding: utf-8 -*-
"""RAG 端到端测试: 文档问答 + 超级记忆 + 动作分类回归。
用临时目录和临时索引, 不碰真实的 rag_index.json。
运行: .venv/Scripts/python.exe rag_test.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parent

# 临时文档目录 + 临时索引, 避免污染真实数据
TEST_DIR = Path(tempfile.mkdtemp(prefix="jarvis_rag_test_"))
os.environ["JARVIS_DOC_DIRS"] = str(TEST_DIR)
os.environ["JARVIS_RAG_INDEX"] = str(TEST_DIR / "rag_index.json")
sys.path.insert(0, str(BASE))

(TEST_DIR / "租房合同.txt").write_text(
    "房屋租赁合同\n第二条 租金与押金\n月租金5000元，押金为一个月租金。\n"
    "第五条 违约金\n任何一方提前解约，需支付违约金人民币8000元。\n",
    encoding="utf-8",
)
(TEST_DIR / "生活笔记.md").write_text(
    "# 生活笔记\n搬家日期定在十二月，新家在朝阳区，离地铁站走路五分钟。\n"
    "搬家当天预约了搬家公司，费用大约1500元。\n",
    encoding="utf-8",
)

import llm_brain  # noqa: E402
import rag_engine  # noqa: E402

FAIL = 0


def check(name, cond, detail=""):
    global FAIL
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAIL += 1


# ---- 1. 建索引 ----
t0 = time.time()
stats = rag_engine.index_docs()
print(f"索引结果: {stats}  耗时 {time.time() - t0:.2f}s")
check("文档索引有片段", bool(stats) and stats["chunks"] > 0, str(stats))

# ---- 2. 语义检索: 合同违约金 ----
hits = rag_engine.search_docs("违约金怎么算")
top = hits[0] if hits else None
check(
    "搜到违约金片段",
    bool(hits) and "违约" in top["text"],
    f"score={top['score']:.3f} text={top['text'][:24]}" if top else "无命中",
)

# ---- 3. 语义检索: 搬家/押金 ----
hits2 = rag_engine.search_docs("房租押金和搬家开销大概多少")
check(
    "搜到租房或搬家内容",
    bool(hits2) and any("押金" in h["text"] or "搬家" in h["text"] for h in hits2),
    f"hits={len(hits2) if hits2 else 0}",
)

# ---- 4. 无关问题应被阈值挡掉 ----
hits3 = rag_engine.search_docs("今天欧冠比赛结果")
check(
    "无关问题返回空",
    hits3 is not None and len(hits3) == 0,
    f"hits={len(hits3) if hits3 is not None else 'None'}",
)

# ---- 5. 记忆语义检索 ----
mem_entries = [
    ("note", "十二月搬家，新家在朝阳区，离地铁五分钟", {"text": "十二月搬家，新家在朝阳区，离地铁五分钟"}),
    ("note", "喜欢喝美式咖啡，不加糖", {"text": "喜欢喝美式咖啡，不加糖"}),
    ("conversation", "用户说：帮我把下载里的报表移到文档",
     {"role": "user", "text": "帮我把下载里的报表移到文档"}),
]
ok = rag_engine.sync_memory(mem_entries)
check("记忆同步成功", ok)
mhits = rag_engine.search_mem("我之前说的搬家的事", kinds=("note",), top_k=2)
check(
    "语义召回到搬家笔记",
    bool(mhits) and "搬家" in mhits[0]["item"]["text"],
    f"score={mhits[0]['score']:.3f}" if mhits else "无命中",
)

# ---- 6. 清空记忆向量 ----
rag_engine.clear_mem()
check("清空后记忆为空", rag_engine.search_mem("搬家", kinds=("note",)) == [])

# ---- 7. 大模型动作分类 ----
config = {}
p = BASE / "config.json"
if p.exists():
    config.update(json.loads(p.read_text(encoding="utf-8")))
u = Path(os.environ.get("APPDATA") or "") / "Jarvis" / "config.json"
if u.exists():
    config.update(json.loads(u.read_text(encoding="utf-8")))

plan = llm_brain.query_llm_plan("我那份租房合同里违约金是多少", config)
print("内容问题计划:", json.dumps(plan, ensure_ascii=False))
check("内容问题判为 ask_doc", plan.get("action") == "ask_doc")

plan2 = llm_brain.query_llm_plan("打开微信", config)
check("普通指令不受影响", plan2.get("action") == "open_app")

# ---- 8. 端到端: 检索 + 生成回答 ----
hits = rag_engine.search_docs("违约金是多少")
if hits:
    ctx = "\n\n".join(
        f"【片段{i + 1} · {Path(h['path']).name}】\n{h['text']}" for i, h in enumerate(hits)
    )
    ans = llm_brain.query_llm_answer("合同违约金是多少", ctx, config)
    print("生成回答:", ans)
    check("回答包含 8000", ("8000" in ans) or ("八千" in ans))
else:
    check("端到端检索", False, "没有检索到片段")

print()
print(f"失败数: {FAIL}")
sys.exit(1 if FAIL else 0)
