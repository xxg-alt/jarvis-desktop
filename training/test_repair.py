# -*- coding: utf-8 -*-
"""失败修复桩测: 假模型给出修正步, 验证修复路径/最多一次/危险修正步不直接执行。"""
import sys
sys.path.insert(0, "/data/workspace/jarvis-desktop")

import jarvis
import llm_brain

REAL_PERFORM = jarvis._perform_plan
fails = []
calls = []          # _perform_plan 实际收到的动作
chat_calls = []     # 假 _provider_chat 被叫了几次
notes = []          # 汇总播报
reply = {"text": ""}
mode = {"always_fail": False}

# 纯桩: 不碰真实系统、不写任务文件、不弹界面
def fake_perform(plan):
    calls.append(dict(plan))
    exe = str((plan.get("args") or {}).get("exe") or "")
    if exe == "bad.exe" or mode["always_fail"]:
        jarvis._TL.action_ok = False
        return f"启动失败: 系统没能定位到 {exe} 的安装路径"
    jarvis._TL.action_ok = True
    return "已打开" + str((plan.get("args") or {}).get("name") or "")

def fake_chat(config, messages, max_tokens=None, json_mode=False):
    chat_calls.append(list(messages))
    return reply["text"]

jarvis._perform_plan = fake_perform
jarvis._agent_persist = lambda task: None
jarvis._agent_notify = lambda text, spoken=False: notes.append(text)
jarvis._agent_generation_changed = lambda gen: False
jarvis.ui_bridge = None
jarvis._AGENT_RUN.update({"stop": False, "skip": False})
llm_brain._provider_chat = fake_chat

GOOD_REPLY = ('{"action": "open_app", "args": {"exe": "notepad.exe", "name": "记事本"}, '
              '"say": "已打开记事本"}')
DANGER_REPLY = ('{"action": "close_app", "args": {"exe": "WeChat.exe", "name": "微信"}, '
                '"say": "这就关闭微信"}')
gen = {"n": 100}

def run_task(answer):
    calls.clear()
    chat_calls.clear()
    notes.clear()
    reply["text"] = answer
    task = {"goal": "打开记事本", "state": "approved", "created": "桩测",
            "steps": [{"action": "open_app", "args": {"exe": "bad.exe", "name": "记事本"},
                       "say": "这就打开记事本", "status": "pending",
                       "scope": jarvis.AGENT_SCOPE_FREE}]}
    gen["n"] += 1
    jarvis._AGENT_RUN.update({"task": task, "gen": gen["n"]})
    jarvis._agent_loop(task, gen["n"])
    return task

# 1. 正常修复: 失败一步 -> 模型给正确 exe -> 重试成功
task = run_task(GOOD_REPLY)
if len(chat_calls) != 1:
    fails.append(f"修复应只调用一次模型, 实际 {len(chat_calls)}")
if len(calls) != 2 or calls[-1].get("args", {}).get("exe") != "notepad.exe":
    fails.append(f"修正步没有按预期重试: {calls}")
if task["steps"][0].get("status") != "done" or not task["steps"][0].get("repaired"):
    fails.append(f"修复成功后步骤未标成 done/repaired: {task['steps'][0]}")
if not any("第1步经修复完成" in n for n in notes):
    fails.append(f"结果汇总没注明修复完成: {notes}")

# 2. 危险修正步: 只挂起等确认, 绝不直接执行
task = run_task(DANGER_REPLY)
if len(chat_calls) != 1:
    fails.append(f"危险修复也只应调用一次模型, 实际 {len(chat_calls)}")
if len(calls) != 1:
    fails.append(f"危险修正步被直接执行了: {calls}")
step = task["steps"][0]
if task.get("state") != "waiting_user" or step.get("status") != "waiting" \
        or step.get("scope") != jarvis.AGENT_SCOPE_ASK:
    fails.append(f"危险修正步没走确认护栏: state={task.get('state')} step={step}")

# 2b. 用户放行续跑后, 同一任务再失败也不许二次修复
step["status"] = "pending"
step["scope"] = jarvis.AGENT_SCOPE_NOTIFY
gen["n"] += 1
jarvis._AGENT_RUN.update({"task": task, "gen": gen["n"]})
jarvis._agent_loop(task, gen["n"])
if len(chat_calls) != 1:
    fails.append(f"续跑后又调了修复模型, 实际 {len(chat_calls)}")

# 3. 修复后仍失败: 如实记为 failed, 且不重复修复
mode["always_fail"] = True
task = run_task(GOOD_REPLY)
mode["always_fail"] = False
if len(chat_calls) != 1:
    fails.append(f"修复仍失败时不该再修一次, 实际 {len(chat_calls)}")
if task["steps"][0].get("status") != "failed" or "修复后仍失败" not in str(task["steps"][0].get("result")):
    fails.append(f"修复仍失败没有如实记录: {task['steps'][0]}")
if task.get("state") != "failed":
    fails.append(f"全步失败时任务状态应为 failed, 实际 {task.get('state')}")

# 4. 结果回读加厚: 关键动作都能给出结构化文本
texts = [
    jarvis._tool_data_text("open_app", {"ok": True, "summary": "已打开",
                                        "data": {"name": "记事本", "exe": "notepad.exe",
                                                 "path": r"C:\Windows\System32\notepad.exe"}}),
    jarvis._tool_data_text("close_app", {"ok": True, "summary": "已关闭",
                                         "data": {"name": "微信", "exe": "WeChat.exe",
                                                  "remaining": 0}}),
    jarvis._tool_data_text("file_manage", {"ok": True, "summary": "3 项",
                                           "data": {"action": "list", "folder": "下载",
                                                    "total": 3, "items": [{"name": "a.txt"}]}}),
    jarvis._tool_data_text("inspect_system", {"ok": True, "summary": "前 5 名",
                                              "data": {"mode": "top_memory",
                                                       "items": [{"name": "x.exe", "memory_mb": 10}]}}),
]
for key, text in zip(("成功", "目标", "条目数", "模式"), texts):
    if key not in text:
        fails.append(f"结果回读缺 {key}: {text}")
if "实际路径" not in texts[0] or "操作后存在" not in texts[2]:
    fails.append(f"路径/存在性回读缺失: {texts[0]} | {texts[2]}")

# 5. 审查修复轮回归
jarvis._perform_plan = REAL_PERFORM
import io, contextlib, builtins, types, subprocess as _subprocess

# 5a. taskkill 非零退出 -> close_app 失败
_real_run = _subprocess.run
class _R:
    def __init__(self, rc=1, out="", err="not found"):
        self.returncode = rc
        self.stdout = out
        self.stderr = err
try:
    _subprocess.run = lambda *a, **k: _R(1, "", "not found")
    jarvis._TL.action_ok = True
    jarvis._TL.last_tool_text = ""
    say = jarvis._perform_plan({"action":"close_app","args":{"exe":"Nope.exe","name":"Nope"},"say":"关掉"})
    if jarvis._TL.action_ok is not False:
        fails.append(f"taskkill非零退出应失败: action_ok={jarvis._TL.action_ok}")
    if "成功=否" not in jarvis._TL.last_tool_text:
        fails.append(f"close_app失败回读缺成功=否: {jarvis._TL.last_tool_text}")
    if "剩余" not in jarvis._TL.last_tool_text and "无法核验" not in jarvis._TL.last_tool_text:
        fails.append(f"close_app回读应保留剩余/无法核验语义: {jarvis._TL.last_tool_text}")
finally:
    _subprocess.run = _real_run

# 5b. system_tools 导入失败 -> action_ok False 且有失败回读
_real_import = builtins.__import__
def _fake_import(name, *a, **k):
    if name == "system_tools":
        raise ImportError("boom")
    return _real_import(name, *a, **k)
try:
    builtins.__import__ = _fake_import
    jarvis._TL.action_ok = True
    jarvis._TL.last_tool_text = ""
    jarvis._perform_plan({"action":"file_manage","args":{"action":"list","folder":"下载"},"say":"列一下"})
    if jarvis._TL.action_ok is not False:
        fails.append(f"system_tools导入失败应置action_ok=False: {jarvis._TL.action_ok}")
    if "成功=否" not in jarvis._TL.last_tool_text or "系统工具不可用" not in jarvis._TL.last_tool_text:
        fails.append(f"导入失败回读缺失败信息: {jarvis._TL.last_tool_text}")
finally:
    builtins.__import__ = _real_import

# 5c. 回退开文档/仅尝试 -> 回读含无法核验且不含成功=是
import smart_launcher
_real_smart = smart_launcher.smart_launch_app
_real_doc_cands = jarvis._document_candidates
_real_open_doc = jarvis._open_document
try:
    smart_launcher.smart_launch_app = lambda t, n: (True, f"已尝试为您启动{n}", "unverified")
    jarvis._document_candidates = lambda q: ["dummy.txt"]
    jarvis._open_document = lambda name: (True, "已打开dummy.txt")
    jarvis._TL.agent_context = True
    jarvis._TL.action_ok = True
    jarvis._TL.last_tool_text = ""
    jarvis._perform_plan({"action":"open_app","args":{"exe":"WeChat.exe","name":"微信"},"say":"打开微信"})
    text = jarvis._TL.last_tool_text
    if "无法核验" not in text:
        fails.append(f"回退开文档应回读无法核验: {text}")
    if "成功=是" in text:
        fails.append(f"回退开文档不应回读成功=是: {text}")
    # 仅尝试启动
    jarvis._document_candidates = lambda q: []
    jarvis._TL.action_ok = True
    jarvis._TL.last_tool_text = ""
    jarvis._perform_plan({"action":"open_app","args":{"exe":"Mystery.exe","name":"Mystery"},"say":"打开"})
    text2 = jarvis._TL.last_tool_text
    if "无法核验" not in text2 or "成功=是" in text2:
        fails.append(f"仅尝试启动回读应为无法核验: {text2}")
finally:
    smart_launcher.smart_launch_app = _real_smart
    jarvis._document_candidates = _real_doc_cands
    jarvis._open_document = _real_open_doc
    jarvis._TL.agent_context = False

# 5d. res 缺 ok -> 回读为无法核验
text = jarvis._tool_data_text("file_manage", {"summary":"3项","data":{"action":"list","folder":"下载","total":3,"items":[]}})
if "无法核验" not in text or "成功=是" in text or "成功=否" in text:
    fails.append(f"缺ok应回读无法核验: {text}")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS: 修复最多一次、危险修正步走护栏、失败如实记录、结果回读加厚+审查回归")
