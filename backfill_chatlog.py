"""一次性回灌: 把 memory.json 里还剩的 240 条老对话补进对话记录。

为什么需要: memory.json 是滚动窗口, 从今天起每轮都进新日志, 但今天以前的话只在
memory.json 里有一份、而且迟早会被挤出去。这一步把它们落地成 append-only 的记录,
之后就再也丢不了了。

幂等: 每条记录带原始 ts, 已经在某天 jsonl 里出现过的 ts 跳过, 重复跑不会重复写。
"""
import json
import os
from pathlib import Path

import chat_log

MEM = Path(os.environ.get("APPDATA") or "") / "Jarvis" / "memory.json"


def seen_ts(root: Path) -> set:
    got = set()
    for p in root.glob("对话-*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                got.add(round(float(json.loads(line).get("ts") or 0), 3))
            except Exception:
                continue
    return got


def main():
    convs = json.loads(MEM.read_text(encoding="utf-8")).get("conversations") or []
    root = chat_log.log_dir()
    root.mkdir(parents=True, exist_ok=True)
    already = seen_ts(root)

    pairs, cur = [], None
    for c in convs:
        if c.get("role") == "user":
            cur = c
        elif c.get("role") == "assistant" and cur is not None:
            pairs.append((cur, c))
            cur = None

    wrote = skipped = 0
    for u, a in pairs:
        ts = float(u.get("ts") or a.get("ts") or 0)
        if round(ts, 3) in already:
            skipped += 1
            continue
        args = a.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else None
            except Exception:
                args = {"raw": args}
        chat_log.log_turn(u.get("text") or "", a.get("text") or "",
                          a.get("action") or "", args, a.get("source") or "", ts=ts)
        already.add(round(ts, 3))
        wrote += 1
        if wrote % 20 == 0:
            print(f"  已补 {wrote} 轮…", flush=True)
    print(f"回灌完成: 新增 {wrote} 轮, 跳过 {skipped} 轮(已存在), 目录 {root}")
    print(f"统计: {chat_log.stats(root)}")


if __name__ == "__main__":
    main()
