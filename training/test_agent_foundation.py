# -*- coding: utf-8 -*-
"""第 0 轮地基修复回归: 成功位、move 护栏、guard_desc、权限重算、来源、账本。"""
import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis
import llm_brain

fails = []


def check(cond, msg):
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)


def wait_until(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


REAL = {name: getattr(jarvis, name) for name in (
    "AGENT_TASKS_PATH", "_perform_plan", "_peek_screen", "_sensitive_gate",
    "_guard_confirm", "_agent_start", "_agent_notify", "_open_document",
    "_write_document", "speak", "ui_bridge", "webbrowser", "_agent_perform",
    "_encode_jpeg_b64", "_capture_screen_image", "_peek_needs_second_pass",
)}
REAL_QUERY_VISION = llm_brain.query_vision
TMP = Path(tempfile.mkdtemp(prefix="jarvis_agent_foundation_"))
jarvis.AGENT_TASKS_PATH = TMP / "agent_tasks.json"
jarvis.speak = lambda text: None
jarvis.ui_bridge = None


def reset_tasks():
    jarvis.AGENT_TASKS_PATH.unlink(missing_ok=True)


def load_raw():
    return json.loads(jarvis.AGENT_TASKS_PATH.read_text(encoding="utf-8"))


def make_waiting(step, state="waiting_user", source="local"):
    task = {"goal": "回归任务", "state": state,
            "created": time.strftime("%Y-%m-%d %H:%M"), "source": source,
            "steps": [dict(step, status="waiting")]}
    jarvis._agent_save_tasks({"tasks": [task]})
    return task


def run_task(step, source="local", state="waiting_user"):
    task = make_waiting(step, state=state, source=source)
    tasks = load_raw()["tasks"]
    tasks[0] = task
    jarvis._agent_save_tasks({"tasks": tasks})
    return task


# ── 1. 成功位诚实: peek_screen / open_url 及其余动作 ──
try:
    reset_tasks()
    llm_brain.query_vision = lambda *a, **kw: "屏幕上是登录按钮"
    jarvis._encode_jpeg_b64 = lambda img, label=None: ("b64", 100)
    jarvis._capture_screen_image = lambda: object()
    jarvis._peek_needs_second_pass = lambda q, first: False
    jarvis._sensitive_window_hit = lambda: False
    jarvis._TL.agent_context = False

    jarvis._sensitive_gate = lambda *a, **kw: (False, "当前窗口可能含敏感内容，这次看屏跳过，Sir。", None)
    jarvis._perform_plan({"action": "peek_screen", "args": {"question": "这是什么"}})
    check(jarvis._TL.action_ok is False, "1a peek_screen 被敏感闸门跳过 -> action_ok=False")

    jarvis._sensitive_gate = lambda *a, **kw: (True, None, None)
    jarvis._perform_plan({"action": "peek_screen", "args": {"question": "这是什么"}})
    check(jarvis._TL.action_ok is True, "1b peek_screen 视觉成功 -> action_ok=True")

    jarvis._sensitive_gate = lambda *a, **kw: (True, None, None)
    llm_brain.query_vision = lambda *a, **kw: ""
    jarvis._perform_plan({"action": "peek_screen", "args": {"question": "这是什么"}})
    check(jarvis._TL.action_ok is False, "1c peek_screen 视觉无结果 -> action_ok=False")

    jarvis._TL.action_ok = True
    say = jarvis._perform_plan({"action": "open_url", "args": {"url": "javascript:alert(1)"}})
    check(jarvis._TL.action_ok is False and "http" in say,
          "1d open_url 非 http/https 协议 -> action_ok=False")

    with mock.patch.object(jarvis.webbrowser, "open", return_value=False) as opened:
        jarvis._TL.action_ok = True
        jarvis._perform_plan({"action": "open_url", "args": {"url": "https://example.com"}})
    check(jarvis._TL.action_ok is False and opened.called,
          "1e open_url 浏览器返回失败 -> action_ok=False")

    with mock.patch.object(jarvis.webbrowser, "open", return_value=True):
        jarvis._TL.action_ok = False
        jarvis._perform_plan({"action": "open_url", "args": {"url": "https://example.com"}})
    check(jarvis._TL.action_ok is True, "1f open_url 浏览器返回成功 -> action_ok=True")

    jarvis._TL.action_ok = True
    with mock.patch.object(jarvis, "_open_document", return_value=(False, "没找到")):
        jarvis._perform_plan({"action": "open_file", "args": {"name": "不存在.md"}})
    check(jarvis._TL.action_ok is False, "1g open_file 打开失败 -> action_ok=False")

    jarvis._TL.action_ok = True
    jarvis._perform_plan({"action": "set_screenshot_dir", "args": {"dir": "一个含糊的文件夹"}})
    check(jarvis._TL.action_ok is False, "1h set_screenshot_dir 解析不了反问 -> action_ok=False")
finally:
    jarvis._sensitive_gate = REAL["_sensitive_gate"]
    jarvis._perform_plan = REAL["_perform_plan"]
    llm_brain.query_vision = REAL_QUERY_VISION


# ── 2. move 护栏统一: 直连与 agent 同级 ──
try:
    guard_seen, performed = [], []
    jarvis._guard_confirm = lambda desc, **kw: guard_seen.append(desc) or False
    jarvis._perform_plan = lambda plan, source="": performed.append(plan) or "stub"
    plan = llm_brain.sanitize_plan({"action": "file_manage", "args": {"action": "move",
                                   "src": "下载/简历.pdf", "dst": "归档"}, "say": "归档"})
    jarvis.execute_plan(plan)

    agent_scope = jarvis.agent_scope_of("file_manage", {"action": "move"})
    draft = jarvis.agent_draft_plan("归档简历", steps=[{"action": "file_manage",
        "args": {"action": "move", "src": "下载/简历.pdf", "dst": "归档"}, "say": "归档"}])
    step = draft["task"]["steps"][0]
    check(plan.get("danger") is True and len(guard_seen) == 1 and not performed,
          "2a 直连 move: danger=True, 护栏确认且不确认不执行")
    check(agent_scope == jarvis.AGENT_SCOPE_ASK and step["scope"] == jarvis.AGENT_SCOPE_ASK,
          "2b agent move: scope=ask, 与直连同级")
    check("移动文件" in step["say"] and "简历.pdf" in step["say"],
          "2c agent move 挂起话术含具体路径")
finally:
    jarvis._guard_confirm = REAL["_guard_confirm"]
    jarvis._perform_plan = REAL["_perform_plan"]


# ── 3. guard_desc 归一保留 + 放行话术 ──
try:
    reset_tasks()
    jarvis._agent_start = lambda task: {"say": f"开始执行任务「{task['goal']}」"}
    notes = []
    jarvis._agent_notify = lambda text, spoken=False: notes.append(text)
    step = {"action": "run_powershell", "args": {"command": "Remove-Item D:\\secret\\报告.txt"},
            "say": "清理文件", "scope": jarvis.AGENT_SCOPE_ASK}
    draft = jarvis.agent_draft_plan("清理文件", steps=[step])
    saved = load_raw()["tasks"][0]["steps"][0]
    check(draft["task"]["steps"][0].get("guard_desc") == "执行命令：Remove-Item D:\\secret\\报告.txt"
          and "执行命令" in saved.get("guard_desc", ""),
          "3a agent_draft_plan 归一保留 guard_desc(落盘只留脱敏摘要)")
    check("Remove-Item" in saved["say"], "3b 挂起话术含具体命令")

    task = load_raw()["tasks"][0]
    task["state"] = "waiting_user"
    task["steps"][0]["status"] = "waiting"
    jarvis._agent_save_tasks({"tasks": [task]})
    res = jarvis.agent_continue()
    check("Remove-Item" in res.get("say", "") and "执行命令" in res.get("say", ""),
          "3c 放行话术含 guard_desc 的具体命令")
finally:
    jarvis._agent_start = REAL["_agent_start"]
    jarvis._agent_notify = REAL["_agent_notify"]


# ── 4. 放行前权限重算: 步骤被改过则重新挂起 ──
try:
    reset_tasks()
    jarvis._agent_start = lambda task: {"say": f"开始执行任务「{task['goal']}」"}
    notes = []
    jarvis._agent_notify = lambda text, spoken=False: notes.append(text)
    old = {"action": "chat", "args": {"text": "闲聊"}, "say": "旧步骤",
           "status": "waiting", "scope": jarvis.AGENT_SCOPE_NOTIFY}
    changed = {"action": "run_powershell", "args": {"command": "Stop-Process -Name excel"},
               "say": "关进程", "guard_desc": "执行命令：Stop-Process -Name excel",
               "status": "waiting", "scope": jarvis.AGENT_SCOPE_NOTIFY,
               "approved_fingerprint": jarvis._agent_step_fingerprint(old),
               "approved_scope": jarvis.AGENT_SCOPE_NOTIFY}
    run_task(changed)
    res = jarvis.agent_continue()
    data = load_raw()["tasks"][0]
    cur = data["steps"][0]
    check(data["state"] == "waiting_user" and cur["status"] == "waiting"
          and cur["scope"] == jarvis.AGENT_SCOPE_ASK,
          "4a 修复后换成高危步 -> 旧放行作废, 重新挂起 ask")
    check("Stop-Process" in res.get("say", "") and any("Stop-Process" in n for n in notes),
          "4b 重新挂起的确认话术含新命令")

    cur.pop("approved_fingerprint", None)
    cur.pop("approved_scope", None)
    jarvis._agent_save_tasks({"tasks": [data]})
    res = jarvis.agent_continue()
    cur = load_raw()["tasks"][0]["steps"][0]
    check(cur["status"] == "pending" and cur["scope"] == jarvis.AGENT_SCOPE_NOTIFY
          and "Stop-Process" in res.get("say", ""),
          "4c 重新放行的高危步能续跑, 话术仍含命令")
finally:
    jarvis._agent_start = REAL["_agent_start"]
    jarvis._agent_notify = REAL["_agent_notify"]


# ── 5. 来源绑定: 任务记来源, 执行/继续只认同源 ──
try:
    reset_tasks()
    seen = []
    jarvis._perform_plan = lambda plan, source="": seen.append(source) or "stub"
    jarvis._TL.action_ok = True
    jarvis._agent_perform({"action": "chat", "args": {}, "source": "remote"})
    check(seen == ["remote"], "5a _agent_perform 把步骤来源传给 _perform_plan")

    jarvis._agent_start = lambda task: {"say": "已放行"}
    step = {"action": "run_powershell", "args": {"command": "Stop-Process -Name excel"},
            "say": "关进程", "guard_desc": "执行命令：Stop-Process -Name excel",
            "status": "waiting", "scope": jarvis.AGENT_SCOPE_ASK, "source": "remote"}
    run_task(step, source="remote")
    res = jarvis.agent_continue(source="local")
    check("不是当前来源" in res.get("say", "") and
          load_raw()["tasks"][0]["state"] == "waiting_user",
          "5b 跨源 continue 被拒, 任务仍挂起")
    check(jarvis._agent_control_command("继续任务", source="local") is None,
          "5c 遥控「继续任务」不能放行本地/远端错源任务")
    res = jarvis.agent_continue(source="remote")
    check(load_raw()["tasks"][0]["state"] == "approved" and "已放行" in res.get("say", ""),
          "5d 同源 continue 正常放行")

    draft = jarvis.agent_draft_plan("归档", steps=[{"action": "volume",
                                    "args": {"mode": "up"}, "say": "调音量"}], source="remote")
    check(draft["task"]["source"] == "remote" and
          draft["task"]["steps"][0]["source"] == "remote",
          "5e 立项时任务与步骤都记来源")
finally:
    jarvis._perform_plan = REAL["_perform_plan"]
    jarvis._agent_start = REAL["_agent_start"]


# ── 6. 账本原子写 + 锁 + 脱敏, 旧格式可读 ──
try:
    reset_tasks()
    check(isinstance(jarvis._AGENT_TASKS_LOCK, type(threading.RLock())),
          "6a 账本有写锁")
    secret = "TOPSECRET_PLAINTEXT_123456"
    command = "Remove-Item C:\\secret\\token.txt -Force -Password abc123"
    data = {"tasks": [{"goal": "敏感任务", "source": "remote", "steps": [
        {"action": "write_file", "args": {"name": "报告.md", "content": secret}},
        {"action": "run_powershell", "args": {"command": command},
         "result": command, "guard_desc": command},
    ]}]}
    replaces = []
    real_replace = jarvis.os.replace
    with mock.patch.object(jarvis.os, "replace", side_effect=lambda a, b: replaces.append((a, b)) or real_replace(a, b)):
        jarvis._agent_save_tasks(data)
    raw = jarvis.AGENT_TASKS_PATH.read_text(encoding="utf-8")
    stored = load_raw()["tasks"][0]["steps"]
    check(secret not in raw and command not in raw,
          "6b 完整内容和命令原文不落盘")
    check(stored[0]["args"].get("content_len") == len(secret)
          and stored[0]["args"].get("content_sha256") == hashlib.sha256(secret.encode()).hexdigest()[:16]
          and stored[0]["args"].get("content_redacted") is True,
          "6c 内容只存长度+哈希摘要")
    check(stored[1]["args"].get("command_redacted") is True
          and "abc123" not in stored[1]["args"].get("command_masked", ""),
          "6d 命令脱敏后落盘")
    check(len(replaces) == 1 and Path(replaces[0][0]).parent == TMP
          and Path(replaces[0][1]) == jarvis.AGENT_TASKS_PATH
          and not list(TMP.glob(".agent_tasks_*.tmp")),
          "6e 同目录临时文件原子替换, 无临时残留")

    before = raw
    with mock.patch.object(jarvis.json, "dump", side_effect=RuntimeError("boom")):
        jarvis._agent_save_tasks(data)
    check(jarvis.AGENT_TASKS_PATH.read_text(encoding="utf-8") == before,
          "6f 写失败时旧账本不被破坏")

    old = {"tasks": [{"goal": "旧格式", "steps": [
        {"action": "write_file", "args": {"name": "旧.md", "content": "旧内容"}}]}]}
    jarvis.AGENT_TASKS_PATH.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    loaded = jarvis._agent_load_tasks()
    check(loaded == old, "6g 读取端兼容旧格式(完整 content 仍可读)")
finally:
    pass


# ── 7. sanitize_plan 不丢调用方给的 guard_desc ──
try:
    custom = "具体命令：Remove-Item D:\\private\\工资表.xlsx"
    out = llm_brain.sanitize_plan({"action": "run_powershell", "danger": True,
                                   "guard_desc": custom,
                                   "args": {"command": "Remove-Item D:\\private\\工资表.xlsx"}})
    check(out.get("guard_desc") == custom, "7a run_powershell 保留具体 guard_desc")
    move = llm_brain.sanitize_plan({"action": "file_manage", "guard_desc": "移动：C:\\私有\\账本.pdf → 归档",
                                    "args": {"action": "move", "src": "C:\\私有\\账本.pdf"}})
    check(move.get("guard_desc") == "移动：C:\\私有\\账本.pdf → 归档",
          "7b file_manage:move 保留具体 guard_desc")
    auto = llm_brain.sanitize_plan({"action": "file_manage",
                                    "args": {"action": "move", "src": "下载/简历.pdf"}})
    check("简历.pdf" in auto.get("guard_desc", ""), "7c 没有自定义描述时用生成的路径描述")
finally:
    pass


for name in REAL:
    setattr(jarvis, name, REAL[name])
llm_brain.query_vision = REAL_QUERY_VISION

print()
if fails:
    print(f"失败 {len(fails)} 条")
    for item in fails:
        print("  ", item)
    raise SystemExit(1)
print("全部通过 (成功位/护栏/guard_desc/重算/来源/账本/sanitize_plan)")
