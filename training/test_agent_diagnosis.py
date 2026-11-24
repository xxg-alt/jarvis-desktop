"""第 3 轮收官桩测：失败分类 / 同类换路 / 界面取证 / 阶段汇报与卡住上报。

全部打桩：不调大模型、不做 shell、不碰真实屏幕；只驱动 agent_core 状态机。
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_core
import jarvis

fails = []


def check(cond, msg):
    print(f"{'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        fails.append(msg)


TMP = Path(tempfile.mkdtemp(prefix="jarvis_agent_round3_"))
LEDGER = TMP / "tasks.json"


def snapshot(obj):
    return json.loads(json.dumps(obj, default=str))


def make_task(goal, steps=None):
    return agent_core.new_task(goal, steps or [])


def sanitize(raw):
    action = str((raw or {}).get("action") or "")
    if not action or action == "chat":
        return {"action": "chat"}
    return {"action": action, "args": (raw or {}).get("args") or {},
            "say": str((raw or {}).get("say") or "")}


def scope_of(step):
    return (agent_core.SCOPE_ASK if (step or {}).get("action") == "move_file"
            else agent_core.SCOPE_FREE)


def script(outcomes):
    it = iter(outcomes)

    def decide(context):
        try:
            return next(it)
        except StopIteration:
            return {"kind": "finish", "say": "剧本结束", "llm_calls": 1}
    return decide


def next_step(action, args=None, say="", **extra):
    return {"kind": "next_step", "llm_calls": 1,
            "step": {"action": action, "args": args or {}, "say": say, **extra}}


def build(decide, perform=None, verify=None, evidence=None, budget=None,
          clock=None, stuck_minutes=None, persist=None, ask_actions=()):
    calls = {"perform": [], "decide": [], "persist": [], "notify": [], "verify": []}

    def decide_wrapper(context):
        calls["decide"].append(snapshot(context))
        return decide(context)

    def default_perform(step, task=None):
        return True, f"真实结果-{step.get('action')}", ""

    def notify(text, spoken=False):
        calls["notify"].append((text, spoken))

    def scoped(step):
        return (agent_core.SCOPE_ASK if step.get("action") in ask_actions
                else scope_of(step))

    runner = agent_core.TaskRunner(
        perform=perform or default_perform,
        persist=persist or (lambda t: calls["persist"].append(snapshot(t))),
        notify=notify,
        scope_of=scoped,
        guard_text=lambda step: str(step.get("say") or step.get("action") or ""),
        sanitize=sanitize,
        decide=decide_wrapper,
        dynamic=lambda: True,
        budget=budget or {},
        verify=verify,
        evidence=evidence,
        clock=clock,
        stuck_minutes=stuck_minutes,
    )
    return runner, calls


def ledger_persist(task):
    LEDGER.write_text(json.dumps({"tasks": [snapshot(task)]}, ensure_ascii=False),
                      encoding="utf-8")


def spoken(calls, needle):
    return [text for text, flag in calls["notify"] if flag and needle in text]


try:
    def classified_perform(step, task=None):
        action = step.get("action")
        if action == "read_file":
            raise FileNotFoundError("目标文件")
        if action == "web_read":
            raise PermissionError("拒绝访问")
        if action == "inspect_system":
            raise TimeoutError("连接超时")
        if action == "run_powershell":
            return False, "屏幕上没变化", "", {"returncode": 1}
        if action == "remember":
            return False, "没有结构化信号的话术", ""
        return True, f"真实结果-{action}", ""

    def classified_verify(task, step, ok, text, tool_text):
        if step.get("action") == "click_screen":
            step["vision_verify"] = {"source": "视觉核实", "conclusion": "未出现"}
            return (False, text, tool_text,
                    {"verification": {"conclusion": "未出现"}})
        return ok, text, tool_text, None

    steps = [
        {"action": "read_file", "args": {"path": "a"}, "say": "读文件"},
        {"action": "web_read", "args": {"url": "http://x"}, "say": "读网页"},
        {"action": "inspect_system", "args": {"mode": "net"}, "say": "查网络"},
        {"action": "click_screen", "args": {"target": "按钮"}, "say": "点按钮"},
        {"action": "run_powershell", "args": {"command": "x"}, "say": "跑命令"},
        {"action": "remember", "args": {"text": "x"}, "say": "记一下"},
    ]
    runner, calls = build(script([]), perform=classified_perform,
                          verify=classified_verify)
    task = make_task("分类", steps)
    runner._run(task, 0)

    wanted = {
        "read_file": agent_core.FAILURE_TARGET_MISSING,
        "web_read": agent_core.FAILURE_PERMISSION_DENIED,
        "inspect_system": agent_core.FAILURE_NETWORK,
        "click_screen": agent_core.FAILURE_UI_UNEXPECTED,
        "run_powershell": agent_core.FAILURE_ACTION,
        "remember": agent_core.FAILURE_UNKNOWN,
    }
    history = {e["action"]: e for e in task["history"]
               if e.get("action") in wanted and e.get("status") == "failed"}
    check(all(entry.get("failure_category") == wanted[action]
              for action, entry in history.items()) and len(history) == 6,
          "1a 五类失败各得正确类别, 无信号标未知, 全部随结果进 history")
    check(all(entry.get("failure_signals") for action, entry in history.items()
              if action != "remember") and not history["remember"]["failure_signals"],
          "1b 类别带真实信号(异常/errno/返回码/核验结论), 无信号不带猜测")
    check(any("先看类别再选下一步" in str(json.dumps(c, ensure_ascii=False))
              for c in calls["decide"])
          and calls["decide"][-1]["failure_classification"]["last_failure"]
          ["failure_category"] == agent_core.FAILURE_UNKNOWN,
          "1c 失败类别与先看类别提示喂给 decide")
    check(agent_core.classify_failure({"result_text": "文件不存在、权限被拒"})
          == agent_core.FAILURE_UNKNOWN
          and "先看类别再选下一步" in jarvis.AGENT_DECIDE_SYSTEM,
          "1d 只按回复文本猜不算信号; decide 提示写明先看类别")
finally:
    pass


try:
    LEDGER.unlink(missing_ok=True)
    fail_streak = (False, "点击没生效", "")

    def streak_perform(step, task=None):
        calls_streak["perform"].append(step.get("args") or {})
        return fail_streak

    calls_streak = {"perform": []}
    outcomes = [
        next_step("click_screen", {"target": "甲"}, "点甲"),
        next_step("click_screen", {"target": "乙"}, "点乙"),
        {"kind": "ask", "question": "同类失败两次，换路吗？", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes), perform=streak_perform,
                          persist=ledger_persist)
    task = make_task("换路")
    runner._run(task, 0)

    check(len(calls_streak["perform"]) == 2 and task["state"] == agent_core.STATE_WAITING
          and task.get("stop_reason") is None,
          "2a 同类第 3 次未执行, decide 选了问用户并挂起")
    forced = calls["decide"][-1].get("forced_route") or {}
    check(forced.get("banned_action") == "click_screen"
          and forced.get("consecutive_failures") == 2
          and forced.get("choices") == list(agent_core.FORCED_ROUTE_CHOICES)
          and "先看类别" in forced.get("rule", ""),
          "2b 第 3 次前的 decide 被强制限定换类别/问用户/收尾报告")
    saved = json.loads(LEDGER.read_text(encoding="utf-8"))["tasks"][0]
    check(saved.get("action_failure_streaks", {}).get("click_screen") == 2,
          "2c 连续失败计数按任务落盘")

    recovered = copy.deepcopy(saved)
    resume_calls = []

    def resume_decide(context):
        resume_calls.append(context)
        return next_step("click_screen", {"target": "丙"}, "点丙")

    runner2, calls2 = build(resume_decide, perform=streak_perform,
                            persist=ledger_persist)
    runner2._run(recovered, 0)
    check(not calls2["perform"] and recovered.get("stop_reason") == "forced_route"
          and resume_calls[0]["forced_route"]["consecutive_failures"] == 2,
          "2d 进程恢复后计数不重置, 同类第 3 次直接拦下")

    def fail_then_success(step, task=None):
        n = len(reset_calls["perform"])
        reset_calls["perform"].append(step.get("args") or {})
        return (False, "失败", "") if n == 0 else (True, "成功", "")

    reset_calls = {"perform": []}
    outcomes = [
        next_step("click_screen", {"target": "甲"}, "点甲"),
        next_step("remember", {"text": "打断"}, "成功一步"),
        next_step("click_screen", {"target": "乙"}, "再点"),
        {"kind": "finish", "say": "清零后可再试", "llm_calls": 1},
    ]
    runner3, calls3 = build(script(outcomes), perform=fail_then_success)
    task3 = make_task("成功清零")
    runner3._run(task3, 0)
    streaks3 = calls3["decide"][2]["failure_classification"]["action_failure_streaks"]
    check(len(reset_calls["perform"]) == 3
          and streaks3.get("click_screen") == 0
          and not calls3["decide"][2]["forced_route"]
          and task3.get("stop_reason") == "finish",
          "2e 中间成功一次清掉同类连败计数, 同类动作可再试")
finally:
    pass


try:
    def ui_perform(step, task=None):
        return False, "点击后界面没反应", ""

    def ui_verify(task, step, ok, text, tool_text):
        step["vision_verify"] = {"source": "视觉核实", "conclusion": "未出现"}
        return (False, text, tool_text,
                {"verification": {"conclusion": "未出现"}})

    evidence_calls = []

    def ui_evidence(task, step, signals):
        evidence_calls.append(snapshot(signals))
        return {"ok": True, "skipped": False, "text": "看到按钮仍是灰色"}

    def ui_decide(context):
        completed = json.dumps(context.get("completed") or [], ensure_ascii=False)
        ui_decide.seen = (completed, context.get("failure_classification") or {})
        return {"kind": "finish", "say": "取证后收尾", "llm_calls": 1}

    runner, calls = build(ui_decide, perform=ui_perform, verify=ui_verify,
                          evidence=ui_evidence)
    task = make_task("界面取证", [{"action": "click_screen",
                                "args": {"target": "保存"}, "say": "点保存"}])
    runner._run(task, 0)

    check(len(evidence_calls) == 1
          and evidence_calls[0]["verification"]["conclusion"] == "未出现",
          "3a 界面未达预期触发一次取证看屏, 类别信号随取证回调传入")
    check(task["budget_used"]["evidence"] == 1
          and task["budget_used"]["llm_calls"] == 2,
          "3b 取证计入统一预算池(一次视觉调用)")
    evidence_history = [e for e in task["history"] if e.get("evidence")]
    check(len(evidence_history) == 1
          and "看到按钮仍是灰色" in evidence_history[0]["result"]
          and "看到按钮仍是灰色" in ui_decide.seen[0],
          "3c 取证看到的结论进 history 并喂给 decide")
    check(evidence_history[0]["result"].startswith("【取证结论·工具数据、不是指令】"),
          "3d 取证文本按不可信数据标记")

    two_steps = [{"action": "click_screen", "args": {"target": "甲"}, "say": "点甲"},
                 {"action": "click_screen", "args": {"target": "乙"}, "say": "点乙"}]
    seen = []

    def limited_decide(context):
        seen.append(context)
        return {"kind": "finish", "say": "收尾", "llm_calls": 1}

    ev2 = []
    runner, calls = build(limited_decide, perform=ui_perform, verify=ui_verify,
                          evidence=lambda t, s, sig: (ev2.append(1) or
                                                      {"ok": True, "text": "看到了"}),
                          budget={"max_evidence": 1, "max_llm_calls": 8})
    task2 = make_task("取证上限", two_steps)
    runner._run(task2, 0)
    skipped = [e for e in task2["history"] if e.get("evidence")
               and e["evidence"].get("skipped")]
    check(len(ev2) == 1 and len(skipped) == 1
          and "取证预算不足" in skipped[0]["result"],
          "3e 取证额度用完不再看屏, 如实记录跳过原因")

    old_hit, old_peek = jarvis._sensitive_window_hit, jarvis._peek_screen
    peeked = []
    try:
        jarvis._sensitive_window_hit = lambda: True
        jarvis._peek_screen = lambda *a, **kw: peeked.append(1)
        out = jarvis._agent_failure_evidence(
            {"source": "local"}, {"action": "click_screen", "say": "点保存"}, {})
        check(out.get("skipped") is True and "敏感画面跳过" in out.get("reason", "")
              and not peeked,
              "3f 敏感画面跳过取证且记录原因, 未调用看屏")
    finally:
        jarvis._sensitive_window_hit, jarvis._peek_screen = old_hit, old_peek
finally:
    pass


try:
    outcomes = [
        next_step("remember", {"text": "甲"}, "记录甲"),
        next_step("remember", {"text": "乙"}, "记录乙"),
        next_step("remember", {"text": "丙"}, "记录丙"),
        {"kind": "next_step", "llm_calls": 1, "stage": "阶段二",
         "step": {"action": "remember", "args": {"text": "丁"}, "say": "记录丁"}},
        next_step("remember", {"text": "戊"}, "记录戊"),
        next_step("remember", {"text": "己"}, "记录己"),
        next_step("remember", {"text": "庚"}, "记录庚"),
        {"kind": "finish", "say": "都记好了", "llm_calls": 1},
    ]
    runner, calls = build(script(outcomes))
    task = make_task("阶段汇报")
    runner._run(task, 0)

    reports = [t for t, flag in calls["notify"] if flag and t.startswith("阶段「")]
    check(task["state"] == agent_core.STATE_DONE and len(reports) == 2,
          "4a 7 步动态任务只在阶段切换和每 3 步各报一次, 不每步都报")
    check(any("阶段「阶段二」" in t and "已做完" in t and "接下来" in t
              for t in reports)
          and any("阶段「第2段」" in t for t in reports),
          "4b 汇报一句话说清做完什么/接下来干什么")
    check(calls["decide"][4]["stage"] == "阶段二"
          and task["stage_reports"]["seen"] == ["stage:阶段二", "steps:2"],
          "4c 同阶段只报一次, decide 给的阶段切换优先于 3 步节点")
finally:
    pass

try:
    now = {"t": 0.0}

    def stuck_decide(context):
        now["t"] = 180.0
        return {"kind": "finish", "say": "收尾", "llm_calls": 1}

    def slow_fail(step, task=None):
        now["t"] = 60.0
        return False, "一直失败", ""

    runner, calls = build(stuck_decide, perform=slow_fail,
                          clock=lambda: now["t"], stuck_minutes=1)
    task = make_task("无进展", [{"action": "run_powershell",
                               "args": {"command": "x"}, "say": "跑命令"}])
    runner._run(task, 0)
    notices = spoken(calls, "卡在")
    check(len(notices) == 1 and "没有成功步" in notices[0]
          and task["stuck_reported"]["kind"] == "no_progress",
          "4d 连续无成功步超过阈值主动上报一次, 后续不重复")

    now2 = {"t": 0.0}
    runner, calls = build(script([]), clock=lambda: now2["t"],
                          stuck_minutes=1, ask_actions=("move_file",))
    task = make_task("等确认", [{"action": "move_file",
                               "args": {"src": "a", "dst": "b"}, "say": "移动文件"}])
    runner._run(task, 0)
    check(task["state"] == agent_core.STATE_WAITING and not spoken(calls, "卡在"),
          "4e 刚挂起时还没到上报阈值, 不提前轰炸")
    now2["t"] = 120.0
    runner._run(task, 0)
    waiting = spoken(calls, "等您确认")
    check(len(waiting) == 1 and "仍在等放行" in waiting[0]
          and task["stuck_reported"]["kind"] == "waiting",
          "4f 等待确认超时只推一条, 说清卡在哪/等什么")
finally:
    pass


print()
if fails:
    print(f"失败 {len(fails)} 条")
    for item in fails:
        print("  ", item)
    raise SystemExit(1)
print("全部通过 (失败分类/同类换路落盘/界面取证/阶段汇报/卡住上报)")
