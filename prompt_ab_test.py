# -*- coding: utf-8 -*-
"""
Prompt A/B 对照实验
A 组: llm_brain.py 里现有的 SYSTEM_PROMPT (约 900 字, 无示例)
B 组: 精简 + 2 条 few-shot 示例的新版
两组用同一模型 / 同一 temperature / 无记忆上下文, 每条口令各跑一遍,
比较动作命中率、参数质量和耗时。

运行: .venv/Scripts/python.exe prompt_ab_test.py
不修改 llm_brain.py 本身 (B 组通过临时替换模块变量实现, 进程退出即还原)。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import llm_brain  # noqa: E402

NEW_PROMPT = """你是 Windows 桌面语音助手贾维斯 (JARVIS)，用户通过麦克风说话，本地语音识别转成文字后交给你，因此不要声称"我只能接收文字"或"请打字"。
回答用中文，默认 1~3 句，口吻忠诚、专业、有管家气质；除非用户明确要求展开，不要长篇大论。

只能输出纯 JSON (不要 markdown 代码块)，格式：
{"action": "chat|open_app|open_url|web_search|screenshot|lock|volume|file_manage|inspect_system|run_powershell", "args": {}, "say": "给用户的中文回答", "status_say": "仅调用系统工具时填 5~12 字垫话"}

动作规则：
- 闲聊、提问、写代码、请教知识：action="chat"，say 填完整回答，不要 status_say。
- 打开软件：action="open_app"，args={"exe": "软件exe名", "name": "软件中文名"}
- 打开网址：action="open_url"，args={"url": "http(s)://..."}
- 搜索：action="web_search"，args={"query": "搜索关键词"}
- 音量：action="volume"，args={"mode": "up|down|mute"}
- 截图：action="screenshot"；锁屏：action="lock"
- 文件管理 action="file_manage"，只允许三种，且只能操作 桌面/下载/文档 三个目录，不能删除：
  {"action": "list", "folder": "下载"}
  {"action": "search", "pattern": "*.pdf", "folder": "下载"}
  {"action": "move", "src": "下载\\报表.xlsx", "dst": "文档"}
- 系统状态 action="inspect_system"：{"mode": "top_memory"} 或 {"mode": "top_cpu"} 或 {"mode": "port", "target": 8080}
- 执行命令 action="run_powershell"，args={"command": "Get-Process"}；一次只给一条最简单的命令，禁止管道|、重定向>、分号;、反引号和换行；关机、重启、删文件、改注册表、改系统服务一律不要生成。
- 调用 file_manage / inspect_system / run_powershell 时填 status_say（如"正在检查高占用进程"），它只表示正在处理，不能声称已完成。用户要删除文件时如实说做不到。

示例：
用户：打开豆包
你：{"action": "open_app", "args": {"exe": "doubao.exe", "name": "豆包"}, "say": "这就为您打开豆包，Sir"}
用户：下载里有没有pdf文件
你：{"action": "file_manage", "args": {"action": "search", "pattern": "*.pdf", "folder": "下载"}, "say": "我这就找一下下载里的PDF，Sir", "status_say": "正在查找文件"}

如果提供了【记忆上下文】，用它理解指代、延续话题和用户纠正；当前用户输入才是本次唯一任务，不要重复执行历史动作。
"""

# (口令, 期望 action, 参数检查要点描述)
CASES = [
    ("打开微信", "open_app", "exe 应类似 WeChat.exe/wechat.exe"),
    ("打开豆包", "open_app", "exe 应类似 doubao.exe"),
    ("帮我把音量调大一点", "volume", "mode 应为 up"),
    ("下载文件夹里有什么PDF文件", "file_manage", "sub=action 应为 search, pattern 含 pdf"),
    ("谁占用了8080端口", "inspect_system", "mode=port, target=8080"),
    ("把下载里的报表.xlsx移到文档", "file_manage", "sub=move, src 含 报表.xlsx, dst=文档"),
    ("帮我搜一下今天北京的天气", "web_search", "query 含 北京/天气"),
    ("现在哪个进程最吃内存", "inspect_system", "mode=top_memory"),
    ("给我讲个笑话", "chat", "say 应为笑话内容"),
    ("截个图", "screenshot", "args 为空"),
]


def load_config():
    cfg = {}
    p = BASE / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    u = Path(os.environ.get("APPDATA") or "") / "Jarvis" / "config.json"
    if u.exists():
        cfg.update(json.loads(u.read_text(encoding="utf-8")))
    return cfg


def brief(plan):
    """把返回的 plan 压成一行可读文本。"""
    if not isinstance(plan, dict):
        return "非dict"
    if plan.get("error"):
        return f"[调用失败] {plan.get('say', '')[:40]}"
    action = plan.get("action", "?")
    args = plan.get("args") or {}
    a = json.dumps(args, ensure_ascii=False)
    if len(a) > 60:
        a = a[:60] + "…"
    return f"{action} {a}"


def run_group(label, config):
    rows = []
    for cmd, expect, _note in CASES:
        t0 = time.time()
        try:
            plan = llm_brain.query_llm_plan(cmd, config)
        except Exception as exc:
            plan = {"action": "chat", "say": f"异常: {exc}", "error": True}
        dt = time.time() - t0
        ok = isinstance(plan, dict) and not plan.get("error") and plan.get("action") == expect
        rows.append((cmd, expect, brief(plan), "OK" if ok else "MISS", dt, plan))
    return rows


def main():
    config = load_config()
    if not llm_brain.clean_api_key(config.get("api_key") or ""):
        print("没有可用的 API Key, 实验中止")
        return

    print(f"服务商: {config.get('ai_provider')}  模型: {config.get('ai_model') or 'deepseek-chat'}")
    print(f"口令数: {len(CASES)}  每组请求数: {len(CASES)}\n")

    # ---- A 组: 现有 prompt ----
    old_prompt = llm_brain.SYSTEM_PROMPT
    print("=" * 72)
    print("A 组: 现有 SYSTEM_PROMPT")
    print("=" * 72)
    a_rows = run_group("A", config)
    for cmd, expect, got, mark, dt, _ in a_rows:
        print(f"[{mark}] {cmd:<14} 期望={expect:<14} 实得={got}  ({dt:.1f}s)")

    # ---- B 组: 精简+示例版 ----
    llm_brain.SYSTEM_PROMPT = NEW_PROMPT
    print()
    print("=" * 72)
    print("B 组: 精简 + few-shot 示例版")
    print("=" * 72)
    b_rows = run_group("B", config)
    for cmd, expect, got, mark, dt, _ in b_rows:
        print(f"[{mark}] {cmd:<14} 期望={expect:<14} 实得={got}  ({dt:.1f}s)")
    llm_brain.SYSTEM_PROMPT = old_prompt

    # ---- 汇总 ----
    a_hit = sum(1 for r in a_rows if r[3] == "OK")
    b_hit = sum(1 for r in b_rows if r[3] == "OK")
    a_avg = sum(r[4] for r in a_rows) / len(a_rows)
    b_avg = sum(r[4] for r in b_rows) / len(b_rows)
    a_len = len(old_prompt)
    b_len = len(NEW_PROMPT)

    print()
    print("=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"动作命中:       A 组 {a_hit}/{len(CASES)}   B 组 {b_hit}/{len(CASES)}")
    print(f"平均耗时:       A 组 {a_avg:.1f}s   B 组 {b_avg:.1f}s")
    print(f"提示词长度(字): A 组 {a_len}   B 组 {b_len}  (约省 {100 - b_len * 100 // a_len}%)")

    # 保存报告
    lines = [
        f"# Prompt A/B 对照报告  {time.strftime('%Y-%m-%d %H:%M')}",
        f"模型: {config.get('ai_model') or 'deepseek-chat'}  temperature: 0.7  无记忆上下文",
        "",
        "| 口令 | 期望 | A组结果 | B组结果 |",
        "|---|---|---|---|",
    ]
    for (cmd, expect, agot, amark, adt, _), (_, _, bgot, bmark, bdt, __) in zip(a_rows, b_rows):
        lines.append(f"| {cmd} | {expect} | {amark} {agot} ({adt:.1f}s) | {bmark} {bgot} ({bdt:.1f}s) |")
    lines += [
        "",
        f"- 动作命中: A {a_hit}/{len(CASES)}, B {b_hit}/{len(CASES)}",
        f"- 平均耗时: A {a_avg:.1f}s, B {b_avg:.1f}s",
        f"- 提示词长度: A {a_len} 字, B {b_len} 字",
    ]
    (BASE / "prompt_ab_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n报告已存到 prompt_ab_report.md")


if __name__ == "__main__":
    main()
