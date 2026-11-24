# -*- coding: utf-8 -*-
"""第 1 轮智能体循环回归：decide 协议 / 预算 / 打转 / 审批 / 落盘 / 回退。

全部打桩 decide 与执行器，不碰大模型和 Windows 平台能力。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_core
import jarvis
import llm_brain

fails = []


def check(cond, msg):
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)


TMP = Path(tempfile.mkdtemp(prefix="jarvis_agent_loop_"))
REAL_TASKS_PATH = jarvis.AGENT_TASKS_PATH
REAL_START = jarvis._agent_start
REAL_CHAT = llm_brain._provider_chat
jarvis.AGENT_TASKS_PATH = TMP / "agent_tasks.json"
jarvis.speak = lambda text: None
jarvis.ui_bridge = None


def snapshot(obj):
    return json.loads(json.dumps(obj, default=str))


def make_task(goal, steps=None, source="local"):
    task = agent_core.new_task(goal, steps or [])
    task["source"] = source
    for step in task["steps"]:
        step["source"] = source
    return task


def script(outcomes):
    """按剧本依次给 decide 结果，用完给一个收尾结论，防止测试自己死循环。"""
    it = iter(outcomes)

    def decide(context):
        try:
            return next(it)
        except StopIteration:
            return {"kind": "finish", "say": "剧本结束", "llm_calls": 1}
    return decide


def build(decide, perform=None, budget=None, repair=None, dynamic=True,
          persist=None):
    calls = {"perform": [], "decide": [], "persist": [], "notify": []}

    def wrapper(context):
        calls["decide"].append(snapshot(context))
        return decide(context)

    def default_perform(step, task=None):
        calls["perform"].append(snapshot(step))
        return True, f"真实结果-{step.get('action')}", f"工具原文-{step.get('action')}"

    runner = agent_core.TaskRunner(
        perform=perform or default_perform,
        persist=persist or (lambda t: calls["persist"].append(snapshot(t))),
        notify=lambda text, spoken=False: calls["notify"].append((text, spoken)),
        scope_of=jarvis._agent_step_scope,
        guard_text=jarvis._agent_guard_text,
        sanitize=llm_brain.sanitize_plan,
        context=jarvis._agent_decide_context,
        decide=wrapper,
        dynamic=(lambda: dynamic) if isinstance(dynamic, bool) else dynamic,
        budget=budget or {},
        repair=repair,
    )
    return runner, calls


def next_step(action, args, say="", calls=1, **extra):
    return {"kind": "next_step", "llm_calls": calls,
            "step": {"action": action, "args": args, "say": say, **extra}}


def spoken(calls):
    return " ".join(text for text, is_spoken in calls["notify"] if is_spoken)


# ── 1. 三步动态任务：给步/看结果/收尾 ──
try:
    old_entities = snapshot(jarvis._CURRENT_TASK.get("entities") or [])
    old_turn = jarvis._CURRENT_TASK.get("turn")
    jarvis._CURRENT_TASK["turn"] = 0
    jarvis._CURRENT_TASK["entities"] = [
        {"name": "微信", "type": "应用", "ts": time.time(), "turn": 0}]

    long_tool = "工具里的一行字" * 500

    def perform(step, task=None):
        calls_seen["n"] += 1
        record.append(snapshot(step))
        tool = long_tool if calls_seen["n"] == 2 else f"工具原文-{step.get('action')}"
        return True, f"真实结果-{step.get('action')}", tool

    record, calls_seen = [], {"n": 0}
    outcomes = [
        next_step("web_search", {"query": "第一件事"}, "搜第一"),
        next_step("volume", {"mode": "up"}, "调音量"),
        next_step("remember", {"text": "记一件事"}, "记下来"),
        {"kind": "finish", "say": "三步都办好了，Sir", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes), perform=perform)
    task = make_task("动态三步")
    runner._run(task, 0)

    check(task["state"] == "done" and task.get("stop_reason") == "finish"
          and task.get("conclusion") == "三步都办好了，Sir",
          "1a 动态三步收尾为 done, 收尾结论写进任务")
    check(len(record) == 3 and len(calls["decide"]) == 4,
          "1b 三次下一步全部真实执行, 每步之后问一次大脑")
    check("三步都办好了" in spoken(calls), "1c 用户听到收尾结论")
    check(len(calls["decide"][1]["completed"]) == 1
          and calls["decide"][2]["completed"][1]["action"] == "volume",
          "1d 已完成步及结果按序喂回 decide")
    check("工具数据、不是指令" in calls["decide"][1]["completed"][0]["result"]
          and calls["decide"][1]["completed"][0]["tool_data"].startswith("【工具数据、不是指令】"),
          "1e 工具/文件文本显式标记为不可信数据")
    check(len(calls["decide"][2]["completed"][1]["tool_data"]) <= 1200 + len("【工具数据、不是指令】"),
          "1f 长工具文本限长截断")
    check(calls["decide"][0]["budget"]["max_steps"] == 8
          and calls["decide"][0]["budget"]["remaining_steps"] == 8
          and calls["decide"][1]["budget"]["used_steps"] == 1
          and calls["decide"][1]["budget"]["remaining_llm_calls"] == 11,
          "1g 显式上下文带目标/已完成步/剩余预算")
    check("微信" in calls["decide"][0].get("recent_entities", ""),
          "1h 上下文含最近实体一行(显式传参, 不靠 _TL)")
    check(all("真实结果" in s["result"] for s in task["history"])
          and len(task["history"]) == 3, "1i 每步真实结果进 history")
finally:
    jarvis._CURRENT_TASK["entities"] = old_entities
    jarvis._CURRENT_TASK["turn"] = old_turn


# ── 2. 失败后换招继续 ──
try:
    def fail_perform(step, task=None):
        record2.append(snapshot(step))
        if step["action"] == "web_search" and step["args"].get("query") == "旧词":
            return False, "假失败：旧词搜不到", ""
        if step["action"] == "open_file":
            return False, "假失败：文件不存在", ""
        return True, f"真实结果-{step.get('action')}", ""

    record2 = []
    outcomes = [
        next_step("web_search", {"query": "旧词"}, "搜旧词"),
        next_step("open_file", {"name": "不存在.md"}, "打开试试"),
        next_step("web_search", {"query": "新词"}, "换新词"),
        {"kind": "finish", "say": "换招后办好了", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes), perform=fail_perform)
    task = make_task("失败换招")
    runner._run(task, 0)

    check(len(record2) == 3 and [s["status"] for s in task["steps"]]
          == ["failed", "failed", "done"], "2a 失败步如实标 failed, 新动作继续执行")
    check(task["state"] == "done" and "换招后办好了" in task["conclusion"],
          "2b 任务最终完成并给出新结论")
    check(len(calls["decide"]) == 4
          and calls["decide"][2]["completed"][1]["status"] == "failed"
          and "假失败" in calls["decide"][2]["completed"][1]["result"],
          "2c 失败原因作为观察喂给下一次 decide")
    check(record2[1]["action"] == "open_file" and record2[2]["action"] == "web_search",
          "2d decide 确实换了不同 action")
finally:
    pass


# ── 3. 指纹打转检测 ──
try:
    repeated = next_step("web_search", {"query": " 重复  查询 "}, "重复搜")
    outcomes = [repeated, repeated, repeated,
                {"kind": "finish", "say": "不该走到这里", "llm_calls": 1}]
    runner, calls = build(script(outcomes))
    task = make_task("打转")
    runner._run(task, 0)

    check(len(calls["perform"]) == 1 and len(calls["decide"]) == 2,
          "3a 同指纹第 2 次出现即拦下, 没有执行第三次")
    check(task["state"] == "done" and task.get("stop_reason") == "spin"
          and "打转" in task.get("conclusion", ""),
          "3b 强制收尾并如实报告打转")
    check("打转" in spoken(calls) and "没有再重复第三次" in spoken(calls),
          "3c 用户听到打转说明")
finally:
    pass


# ── 4. 预算耗尽（步数 / LLM 调用 / 时长）──
try:
    outcomes = [
        next_step("web_search", {"query": "甲"}, "甲"),
        next_step("web_search", {"query": "乙"}, "乙"),
    ]
    runner, calls = build(script(outcomes), budget={"max_steps": 1})
    task = make_task("步数预算")
    runner._run(task, 0)
    check(len(calls["perform"]) == 1 and task["state"] == "failed"
          and task.get("stop_reason") == "budget_steps"
          and "步数预算" in spoken(calls),
          "4a 步数预算耗尽: 停下并说明卡在步数")

    outcomes = [
        next_step("web_search", {"query": "甲"}, "甲"),
        next_step("web_search", {"query": "乙"}, "乙"),
    ]
    runner, calls = build(script(outcomes), budget={"max_llm_calls": 1})
    task = make_task("调用预算")
    runner._run(task, 0)
    check(len(calls["perform"]) == 1 and len(calls["decide"]) == 1
          and task.get("stop_reason") == "budget_llm"
          and "LLM 调用预算" in spoken(calls),
          "4b LLM 调用预算耗尽: 不再问大脑, 说明卡在决策")

    slow_record = []

    def slow_perform(step, task=None):
        slow_record.append(step["action"])
        time.sleep(0.05)
        return True, "慢结果", ""

    runner, calls = build(script([]), perform=slow_perform,
                          budget={"max_seconds": 0.01})
    task = make_task("时长预算", [{"action": "web_search",
                                 "args": {"query": "慢"}, "say": "慢查"}])
    runner._run(task, 0)
    check(len(slow_record) == 1 and task.get("stop_reason") == "budget_time"
          and "时长预算" in spoken(calls),
          "4c 时长预算耗尽: 停下并说明卡在时长")
finally:
    pass


# ── 5. decide 给 ask 级新步：挂起 / 跨源无效 / 同源放行 ──
try:
    outcomes = [
        next_step("file_manage", {"action": "move", "src": "下载/简历.pdf",
                                  "dst": "文档"}, "归档简历"),
        {"kind": "finish", "say": "归档完成", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes))
    task = make_task("高危新步", [{"action": "web_search",
                                "args": {"query": "查一下"}, "say": "先查"}])
    runner._run(task, 0)
    ask_step = task["steps"][1]

    check(task["state"] == "waiting_user" and ask_step["status"] == "waiting"
          and ask_step["scope"] == jarvis.AGENT_SCOPE_ASK
          and len(calls["perform"]) == 1,
          "5a 新增 ask 级步挂起, 不拿立项授权直接执行")
    check("移动文件：下载/简历.pdf → 文档" in spoken(calls)
          and "继续任务" in spoken(calls),
          "5b 确认话术含 guard 文本(具体路径)")

    resumed = []

    def fake_start(t):
        resumed.append(t)   # agent_continue 从账本重新加载的任务对象
        runner._run(t, 0)
        return {"say": "已放行"}

    jarvis._agent_start = fake_start
    jarvis._agent_save_tasks({"tasks": [task]})
    res = jarvis.agent_continue(source="remote")
    stored = json.loads(jarvis.AGENT_TASKS_PATH.read_text(encoding="utf-8"))
    check("不是当前来源" in res.get("say", "")
          and stored["tasks"][0]["state"] == "waiting_user"
          and len(calls["perform"]) == 1,
          "5c 跨源放行无效, 任务仍挂起且没有多执行一步")

    res = jarvis.agent_continue(source="local")
    finished = resumed[0] if resumed else {}
    check(len(calls["perform"]) == 2 and finished.get("state") == "done"
          and finished.get("steps", [{}])[1].get("status") == "done"
          and finished.get("conclusion") == "归档完成"
          and "移动文件" in res.get("say", ""),
          "5d 同源放行后新步执行, 大脑给出收尾结论")
finally:
    jarvis._agent_start = REAL_START


# ── 6. decide 吐垃圾 / 空 / 不合法步：明确失败，不循环 ──
try:
    for label, outcome in (("垃圾类型", {"kind": "garbage"}),
                           ("空结果", {}),
                           ("参数不合法", next_step("open_app", {}))):
        runner, calls = build(script([outcome]))
        task = make_task(label)
        runner._run(task, 0)
        check(task["state"] == "failed" and task.get("stop_reason") == "decide_invalid"
              and len(calls["decide"]) == 1 and not calls["perform"]
              and "没有给出可执行的下一步" in spoken(calls),
              f"6 {label}: 明确失败状态, 只问一次不循环")

    real_chat = llm_brain._provider_chat
    llm_brain._provider_chat = lambda config, messages, **kw: (
        '```json\n{"kind":"finish","say":"模型说完了"}\n```')
    try:
        out = jarvis._agent_decide({"goal": "测试"})
        check(out.get("kind") == "finish" and out.get("say") == "模型说完了"
              and out.get("llm_calls") == 1,
              "6d jarvis 侧 decide 调 llm_brain 并解析 JSON")
    finally:
        llm_brain._provider_chat = real_chat

    llm_brain._provider_chat = lambda config, messages, **kw: "这不是 JSON"
    try:
        out = jarvis._agent_decide({"goal": "测试"})
        check(out.get("kind") == "invalid" and out.get("llm_calls") == 1,
              "6e 模型吐垃圾 -> invalid, 不重试")
    finally:
        llm_brain._provider_chat = real_chat

    blank_calls = []

    def blank(config, messages, **kw):
        blank_calls.append(1)
        return ""

    llm_brain._provider_chat = blank
    try:
        out = jarvis._agent_decide({"goal": "测试"})
        check(out.get("kind") == "invalid" and len(blank_calls) == 2,
              "6f 空白返回最多换模式重发一次, 不无限追问")
    finally:
        llm_brain._provider_chat = real_chat

    check(jarvis._agent_sanitize_step(
        {"action": "run_powershell", "args": {"command": "Get-Process"}})["action"]
        == "run_powershell"
        and jarvis._agent_sanitize_step({"action": "open_app", "args": {}}) is None,
        "6g jarvis 侧归一关: 合法步保留, 缺参数步剔除")
finally:
    llm_brain._provider_chat = REAL_CHAT


# ── 7. 结果先落账本再继续 ──
try:
    jarvis.AGENT_TASKS_PATH.unlink(missing_ok=True)
    midway = []

    def ledger_persist(task):
        jarvis._agent_persist(task)

    def checking_perform(step, task=None):
        seen.append(step["action"])
        if len(seen) == 2:
            midway.append(json.loads(
                jarvis.AGENT_TASKS_PATH.read_text(encoding="utf-8")))
        return True, f"真实结果-{step['action']}", ""

    seen = []
    runner, calls = build(script([{"kind": "finish", "say": "都做完了", "llm_calls": 1}]),
                          perform=checking_perform, persist=ledger_persist)
    task = make_task("中途查账本", [
        {"action": "web_search", "args": {"query": "先查"}, "say": "先查"},
        {"action": "remember", "args": {"text": "再记"}, "say": "再记"},
    ])
    runner._run(task, 0)

    saved = midway[0]["tasks"][0]["steps"][0] if midway else {}
    check(saved.get("status") == "done" and "真实结果-web_search" in saved.get("result", ""),
          "7a 第 2 步开始前, 第 1 步真实结果已在账本里")
    check(len(midway) == 1 and task["state"] == "done"
          and len(calls["persist"]) == 0,   # 自定义 persist 不走 calls 记录
          "7b 账本落盘后再继续, 最终收尾正常")
finally:
    pass


# ── 8. agent_dynamic 关 -> 固定计划旧路径 ──
try:
    old_flag = jarvis.CONFIG.get("agent_dynamic")
    jarvis.CONFIG["agent_dynamic"] = False

    def explode(context):
        raise AssertionError("关掉开关后不该再问大脑")

    runner, calls = build(explode, dynamic=jarvis._agent_dynamic_enabled)
    task = make_task("旧路径", [
        {"action": "web_search", "args": {"query": "一步"}, "say": "一步"},
        {"action": "remember", "args": {"text": "两步"}, "say": "两步"},
    ])
    runner._run(task, 0)

    check(not calls["decide"] and len(calls["perform"]) == 2
          and task["state"] == "done",
          "8a 开关关掉: 固定计划照旧执行, 不调 decide")
    check("budget" not in task and "history" not in task
          and not task.get("conclusion"),
          "8b 旧路径行为不变(无预算字段/无动态结论)")
finally:
    if old_flag is None:
        jarvis.CONFIG.pop("agent_dynamic", None)
    else:
        jarvis.CONFIG["agent_dynamic"] = old_flag

check(jarvis.CONFIG.get("agent_dynamic", True) is True,
      "8c agent_dynamic 默认开, 可配置回退")


# ── 9. 修复与重规划共用一份预算池 ──
try:
    seq = []

    def fail_then_fix(step, task=None):
        seq.append(step["action"])
        if len(seq) == 1:
            return False, "原始失败", ""
        return True, "修复后真实结果", ""

    def repair(task, step, error_text):
        calls_repair.append(error_text)
        return {"action": "web_search", "args": {"query": "改好了"},
                "say": "修复重试", "scope": jarvis.AGENT_SCOPE_FREE}, ""

    calls_repair = []
    runner, calls = build(script([{"kind": "finish", "say": "不该走到", "llm_calls": 1}]),
                          perform=fail_then_fix, repair=repair,
                          budget={"max_llm_calls": 1, "max_steps": 8})
    task = make_task("修复共用预算", [{"action": "web_search",
                                  "args": {"query": "原始"}, "say": "原始"}])
    runner._run(task, 0)

    check(len(calls_repair) == 1 and len(seq) == 2
          and task["budget_used"]["llm_calls"] == 1
          and task["budget_used"]["steps"] == 2,
          "9a 修复调用+重跑同池计费(1 次 LLM + 1 步)")
    check(task.get("stop_reason") == "budget_llm" and not calls["decide"],
          "9b 修复吃满 LLM 预算后不再重规划, 明确停下")
finally:
    pass


# ── 10. decide 的第三种结果：向用户提问，反复问也算打转 ──
try:
    outcomes = [
        {"kind": "ask", "question": "选 A 还是 B？", "llm_calls": 1},
        {"kind": "finish", "say": "按 A 办好了", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes))
    task = make_task("提问")
    runner._run(task, 0)
    check(task["state"] == "waiting_user"
          and task.get("pending_question") == "选 A 还是 B？"
          and "选 A 还是 B？" in spoken(calls) and not calls["perform"]
          and task["budget_used"] == {"steps": 0, "llm_calls": 1,
                                      "seconds": task["budget_used"]["seconds"]},
          "10a ask 结果挂起提问, 不执行步骤, 只花一次 LLM 调用")

    resumed = []
    jarvis._agent_start = lambda t: (resumed.append(t), runner._run(t, 0),
                                     {"say": "已放行"})[2]
    jarvis._agent_save_tasks({"tasks": [task]})
    jarvis.agent_continue(source="local")
    check(resumed and resumed[0]["state"] == "done"
          and resumed[0].get("conclusion") == "按 A 办好了"
          and any("用户已看过提问并放行继续" in h["result"]
                  for h in resumed[0]["history"]),
          "10b 放行后问题作为观察进 history, 大脑给出结论")

    again = {"kind": "ask", "question": "还是这个问题？", "llm_calls": 1}
    runner2, calls2 = build(script([again, again]))
    task2 = make_task("反复问")
    runner2._run(task2, 0)
    check(task2["state"] == "waiting_user" and len(calls2["decide"]) == 1,
          "10c 第 1 次提问先挂起")
    task2["state"] = "approved"   # 用户放行后再跑, 才会问到第二次
    runner2._run(task2, 0)
    check(task2.get("stop_reason") == "spin" and len(calls2["decide"]) == 2
          and "打转" in task2.get("conclusion", "")
          and "打转" in spoken(calls2),
          "10d 反复问同一句在第 2 次拦下, 强制收尾")
finally:
    jarvis._agent_start = REAL_START


# ── 11. 崩溃恢复: 执行前已落 started, 恢复标结果未知, 绝不重放 ──
try:
    class Crash(BaseException):
        """模拟进程硬中断: 不是 Exception, _run 的普通异常兜底抓不到。"""
        pass

    crash_ledger = TMP / "crash_ledger.json"
    perform_snapshots = []

    def crash_persist(task):
        crash_ledger.write_text(json.dumps({"tasks": [snapshot(task)]},
                                           ensure_ascii=False), encoding="utf-8")

    def crashing_perform(step, task=None):
        perform_snapshots.append(snapshot(task))
        raise Crash("模拟进程在执行器中崩溃")

    runner, calls = build(script([]), perform=crashing_perform,
                          persist=crash_persist, budget={"max_steps": 4})
    crash_task = make_task("崩溃恢复", [
        {"action": "write_file", "args": {"name": "a.md", "content": "正文"},
         "say": "写文件", "scope": jarvis.AGENT_SCOPE_NOTIFY}])
    try:
        runner._run(crash_task, 0)
    except Crash:
        pass

    at_perform = perform_snapshots[0]
    check(at_perform["steps"][0]["status"] == "started"
          and at_perform["budget_used"]["steps"] == 1,
          "11a 调执行器前: 步已标 started 且步数预算已计入账本")
    on_disk = json.loads(crash_ledger.read_text(encoding="utf-8"))["tasks"][0]
    check(on_disk["steps"][0]["status"] == "started",
          "11b 崩溃时账本停在 started, 没有假装 pending 或 done")

    loop_ledger = jarvis.AGENT_TASKS_PATH
    jarvis.AGENT_TASKS_PATH = crash_ledger
    while not jarvis._AGENT_EVENTS.empty():
        jarvis._AGENT_EVENTS.get()
    try:
        recovered = jarvis._agent_load_tasks()["tasks"][0]
        rec_step = recovered["steps"][0]
        check(rec_step["status"] == jarvis.AGENT_STEP_UNKNOWN
              and "结果未知" in rec_step.get("result", "")
              and "需核实" in rec_step.get("unknown_note", "")
              and recovered["state"] == "waiting_user"
              and recovered.get("stop_reason") == "needs_verification",
              "11c 恢复后 started 步一律标结果未知/需核实, 任务挂起不续跑")
        notices = []
        while not jarvis._AGENT_EVENTS.empty():
            notices.append(jarvis._AGENT_EVENTS.get())
        check(any("结果未知" in text and "未重跑" in text for text in notices),
              "11d 用户听到结果未知、未重跑的说明")

        replayed = []

        def no_run(t):
            replayed.append(t)
            return {"say": "不该被调用"}

        jarvis._agent_start = no_run
        res = jarvis.agent_continue(source="local")
        check(not replayed and "需核实" in res.get("say", "")
              and json.loads(crash_ledger.read_text(encoding="utf-8"))["tasks"][0]
              ["steps"][0]["status"] == jarvis.AGENT_STEP_UNKNOWN,
              "11e 放行也不能重放结果未知步, 账本状态不变")
    finally:
        jarvis._agent_start = REAL_START
        jarvis.AGENT_TASKS_PATH = loop_ledger

    # 修复重跑同样先落 started
    last_persist, attempts, at_repair = [], [], []

    def fail_then_crash(step, task=None):
        attempts.append(step["action"])
        if len(attempts) == 1:
            return False, "原始失败", ""
        at_repair.append(last_persist[-1])
        raise Crash("修复重跑时崩溃")

    def capture(task):
        last_persist.append(snapshot(task))

    def fake_repair(task, step, error_text):
        return {"action": "web_search", "args": {"query": "改"}, "say": "重试",
                "scope": jarvis.AGENT_SCOPE_FREE}, ""

    runner, calls = build(script([]), perform=fail_then_crash, repair=fake_repair,
                          persist=capture)
    repair_task = make_task("修复崩溃", [
        {"action": "open_file", "args": {"name": "x"}, "say": "x"}])
    try:
        runner._run(repair_task, 0)
    except Crash:
        pass
    snap = at_repair[0]
    check(len(attempts) == 2 and snap["steps"][0]["status"] == "started"
          and snap["steps"][0].get("repair_attempted") is True
          and snap["budget_used"]["steps"] == 2,
          "11f 修复重跑执行前也先落 started, 重跑步与修复调用同池计费")
finally:
    jarvis.AGENT_TASKS_PATH = TMP / "agent_tasks.json"
    jarvis._agent_start = REAL_START


# ── 12. 指纹: 完整参数值哈希 / 提问空白归一化 ──
try:
    prefix = "前200字完全一样" * 30
    fp_a = agent_core.step_fingerprint(
        {"action": "web_search", "args": {"query": prefix + "北京"}})
    fp_b = agent_core.step_fingerprint(
        {"action": "web_search", "args": {"query": prefix + "上海"}})
    check(fp_a != fp_b, "12a 前200字相同、后缀不同的查询不误判为打转")

    check(agent_core.step_fingerprint(
              {"action": "web_search", "args": {"query": "a  b"}})
          != agent_core.step_fingerprint(
              {"action": "web_search", "args": {"query": "a b"}}),
          "12b 步骤参数不压空白, 值全量入哈希")
    check(agent_core.step_fingerprint(
              {"action": "web_search", "args": {"q": "x", "n": 1}})
          == agent_core.step_fingerprint(
              {"action": "web_search", "args": {"n": 1, "q": "x"}}),
          "12c 只做类型明确的规范化: 字典键排序后等价")

    outcomes = [next_step("write_file", {"name": "a.md", "content": prefix + "北京"}, "写北京"),
                next_step("write_file", {"name": "b.md", "content": prefix + "上海"}, "写上海"),
                {"kind": "finish", "say": "两条都写了", "llm_calls": 1}]
    runner, calls = build(script(outcomes))
    task = make_task("长内容不误判")
    runner._run(task, 0)
    check(len(calls["perform"]) == 2 and task.get("stop_reason") == "finish"
          and task.get("conclusion") == "两条都写了",
          "12d 前200字相同后缀不同的两个内容先后执行, 没有被打转检测拦下")

    check(agent_core.ask_fingerprint(" 选 A   还是 B？ ")
          == agent_core.ask_fingerprint("选 A 还是 B？"),
          "12e 提问指纹先空白归一化再哈希")
finally:
    pass


# ── 13. 预算拆开 + 预算退出不许冒充 done ──
try:
    budget = {"max_steps": 1, "max_llm_calls": 12}
    check(agent_core.TaskRunner._step_budget_reason(
              budget, {"steps": 1, "llm_calls": 0}, 0.0) == "budget_steps"
          and agent_core.TaskRunner._llm_budget_reason(
              budget, {"steps": 1, "llm_calls": 0}, 0.0) is None,
          "13a 步数耗尽只挡执行, 不挡 LLM 调用")
    budget = {"max_steps": 8, "max_llm_calls": 1}
    check(agent_core.TaskRunner._llm_budget_reason(
              budget, {"steps": 7, "llm_calls": 1}, 0.0) == "budget_llm"
          and agent_core.TaskRunner._step_budget_reason(
              budget, {"steps": 7, "llm_calls": 1}, 0.0) is None,
          "13b LLM 调用耗尽只挡决策/修复, 不挡执行")

    outcomes = [next_step("web_search", {"query": "1"}, "一"),
                next_step("web_search", {"query": "2"}, "二"),
                next_step("web_search", {"query": "3"}, "三"),
                {"kind": "finish", "say": "假装完成", "llm_calls": 1}]
    runner, calls = build(script(outcomes), budget={"max_llm_calls": 3})
    task = make_task("成功后预算耗尽")
    runner._run(task, 0)
    check(len(calls["perform"]) == 3 and task["state"] == "failed"
          and task.get("stop_reason") == "budget_llm"
          and task.get("budget_exhausted") is True
          and "假装完成" not in task.get("conclusion", ""),
          "13c 三步成功后因 LLM 预算退出: 落失败+原因, 不冒充普通 done")

    runner, calls = build(script([{"kind": "finish", "say": "假装完成", "llm_calls": 1}]),
                          budget={"max_steps": 2})
    task = make_task("步数满后不收尾", [
        {"action": "web_search", "args": {"query": "一"}, "say": "一"},
        {"action": "web_search", "args": {"query": "二"}, "say": "二"}])
    runner._run(task, 0)
    check(task["state"] == "failed" and task.get("stop_reason") == "budget_steps"
          and "假装完成" not in task.get("conclusion", ""),
          "13d 步数预算已满后 decide 即使给 finish, 也不许落普通 done")
finally:
    pass


jarvis.AGENT_TASKS_PATH = REAL_TASKS_PATH
jarvis._agent_start = REAL_START
llm_brain._provider_chat = REAL_CHAT

print()
if fails:
    print(f"失败 {len(fails)} 条")
    for item in fails:
        print("  ", item)
    raise SystemExit(1)
print("全部通过 (decide 协议/预算/打转/审批挂起/落盘/回退)")
