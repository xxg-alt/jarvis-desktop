# -*- coding: utf-8 -*-
"""执行级考试: 验"步骤之间传结果"真的接通了。

E1: 在下载里造 3 个 PDF + 1 个 txt -> 目标"找出来列清单写文档"
    -> 文档里必须有 3 个 PDF 名, 且不能有 txt 名。
E2: "先看什么最吃内存, 再把结果写成文档" -> 文档里必须带真实内存数字 (MB)。

用法: python training/run_exam_exec.py --url https://xxxx/v1
"""
import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run_goal(jarvis, goal, timeout=240):
    r = jarvis.agent_draft_plan(goal)
    if "task" not in r:
        raise RuntimeError(f"立项失败: {r}")
    jarvis.agent_approve()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        tasks = jarvis._agent_load_tasks().get("tasks", [])
        t = tasks[-1] if tasks else {}
        if t.get("state") in ("done", "failed"):
            return t
    raise TimeoutError(goal)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()
    import jarvis
    jarvis.CONFIG.update({"ai_provider": "Kaggle 免费 35B", "ai_url": args.url,
                          "ai_model": "", "api_key": ""})

    dl = Path.home() / "Downloads"
    dl.mkdir(exist_ok=True)
    for n in ["报告甲.pdf", "合同乙.pdf", "笔记丙.pdf", "杂项.txt"]:
        (dl / n).write_bytes(b"%PDF-fake" if n.endswith(".pdf") else b"x")
    ws = jarvis.agent_workspace()
    print("workspace:", ws, flush=True)
    for f in list(ws.glob("*.md")) + list(ws.glob("*.txt")):
        if any(k in f.name for k in ("PDF", "清单", "内存", "检查")):
            f.unlink()

    t1 = run_goal(jarvis, "把下载里的PDF都找出来，列个清单文档存到工作区")
    print("E1 state:", t1["state"],
          [(s["action"], s["status"]) for s in t1["steps"]], flush=True)
    content = ""
    for f in ws.glob("*"):
        if f.is_file() and f.suffix in (".md", ".txt"):
            c = f.read_text(encoding="utf-8", errors="ignore")
            if "清单" in f.name or "PDF" in f.name or "报告甲" in c:
                content += c
    ok1 = all(n in content for n in ["报告甲.pdf", "合同乙.pdf", "笔记丙.pdf"]) and "杂项" not in content
    print("E1 content_ok:", ok1, flush=True)
    print(content[:260], flush=True)

    t2 = run_goal(jarvis, "先看看电脑什么最吃内存，再把检查结果写成一个文档")
    print("E2 state:", t2["state"],
          [(s["action"], s["status"]) for s in t2["steps"]], flush=True)
    c2 = ""
    for f in ws.glob("*"):
        if f.is_file() and f.suffix in (".md", ".txt"):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if "内存" in txt and "MB" in txt:
                c2 += txt
    ok2 = bool(re.search(r"\d+(\.\d+)?\s*MB", c2))
    print("E2 content_ok:", ok2, flush=True)
    print(c2[:260], flush=True)
    print("RESULT E1", ok1, "E2", ok2, flush=True)
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
