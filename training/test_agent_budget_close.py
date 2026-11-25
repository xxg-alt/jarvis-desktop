# -*- coding: utf-8 -*-
"""收尾裁定回归：仅放行已完成动态任务的 finish，LLM 预算不豁免。"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_core


fails = []


def check(condition, message):
    print(f"{'PASS' if condition else 'FAIL'}  {message}")
    if not condition:
        fails.append(message)


def sanitize(step):
    if not isinstance(step, dict) or not step.get("action"):
        return None
    return {"action": step["action"], "args": step.get("args") or {},
            "say": step.get("say") or ""}


def next_step(number):
    return {"kind": "next_step", "llm_calls": 1,
            "step": {"action": "remember", "args": {"n": number},
                      "say": f"执行第 {number} 步"}}


def build(decide, perform=None, dynamic=True, budget=None, persist=None):
    calls = {"decide": [], "perform": [], "persist": [], "notify": []}

    def decide_wrapper(context):
        calls["decide"].append(copy.deepcopy(context))
        return decide(context)

    def default_perform(step, task=None):
        calls["perform"].append(copy.deepcopy(step))
        return True, f"完成 {step.get('args', {}).get('n', '?')}", ""

    runner = agent_core.TaskRunner(
        perform=perform or default_perform,
        persist=persist or (lambda task: calls["persist"].append(copy.deepcopy(task))),
        notify=lambda text, spoken=False: calls["notify"].append((text, spoken)),
        decide=decide_wrapper,
        sanitize=sanitize,
        dynamic=lambda: dynamic,
        budget=budget,
    )
    return runner, calls


def run_dynamic(outcomes, max_steps, perform=None, persist=None,
                max_llm_calls=20):
    script = iter(outcomes)

    def decide(context):
        return next(script)

    runner, calls = build(decide, perform=perform,
                          budget={"max_steps": max_steps,
                                  "max_llm_calls": max_llm_calls}, persist=persist)
    task = agent_core.new_task("预算收尾", [])
    runner._run(task, 0)
    return task, runner, calls


# T1: N 次真实步骤打满步数后，普通 decide 返回 finish。
try:
    n = 3
    task, _, calls = run_dynamic(
        [next_step(i) for i in range(n)]
        + [{"kind": "finish", "say": "N 步都办好了", "llm_calls": 1}], n)
    check(task.get("state") == agent_core.STATE_DONE
          and task.get("stop_reason") == "finish"
          and task.get("conclusion") == "N 步都办好了",
          "T1 步数恰好用完时，收尾裁定 finish -> done/finish（修前会是 failed/budget_steps）")
    check(len(calls["perform"]) == n
          and len(calls["decide"]) == n + 1
          and calls["decide"][-1]["budget"]["remaining_steps"] == 0,
          "T1 只执行 N 步，最后一次普通 decide 如实看到步数已满")
finally:
    pass


# LLM 预算用尽后不再调用 decide；步数豁免也不能用于 ask。
try:
    task, _, calls = run_dynamic(
        [next_step(1), {"kind": "finish", "say": "调用预算下完成", "llm_calls": 1}],
        10, max_llm_calls=1)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_llm"
          and task.get("budget_used", {}).get("llm_calls") == 1
          and len(calls["decide"]) == 1,
          "补充：LLM 预算已满时不再问大脑，不额外调用 finish")
    check(any("已执行步骤都成功完成" in text and "没能正式收尾" in text
              and "卡在还没做完" not in text
              for text, spoken in calls["notify"] if spoken),
          "补充：LLM 预算退出如实报告步骤已完成、未能正式收尾")

    task, _, calls = run_dynamic(
        [next_step(1), {"kind": "ask", "question": "还需要确认哪一项？",
                        "llm_calls": 1}],
        1)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_steps"
          and not task.get("pending_question")
          and len(calls["decide"]) == 2,
          "补充：步数已满时 ask 不享受 finish 的收尾放行")
    check(any("已执行步骤都成功完成" in text and "没能正式收尾" in text
              and "卡在还没做完" not in text
              for text, spoken in calls["notify"] if spoken),
          "补充：步数预算退出保留步骤已完成、未能正式收尾的如实文案")
finally:
    pass


# T2: 步数满后普通 decide 返回 next_step，新步不执行并落预算失败。
try:
    n = 2
    extra_step = next_step("不该执行")
    task, _, calls = run_dynamic(
        [next_step(i) for i in range(n)] + [extra_step], n)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_steps"
          and len(calls["perform"]) == n
          and len(calls["decide"]) == n + 1,
          "T2 步数满后 next_step -> failed/budget_steps，且新步没有执行")
    check(len(task.get("steps", [])) == n
          and all(step.get("status") == agent_core.STATE_DONE
                  for step in task["steps"]),
          "T2 超预算的新步没有进入任务步骤账本")
finally:
    pass


# T3: 最后一步失败时不得用 finish 冒充完成。
try:
    performed = []

    def fail_perform(step, task=None):
        performed.append(step.get("action"))
        return False, "最后一步失败", ""

    task, _, calls = run_dynamic(
        [next_step(1), {"kind": "finish", "say": "不应收尾", "llm_calls": 1}],
        1, perform=fail_perform)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_steps"
          and task.get("conclusion") != "不应收尾"
          and len(performed) == 1,
          "T3 末步失败且步数打满 -> failed，finish 不能冒充完成")
    check(len(calls["decide"]) == 2
          and calls["decide"][-1]["completed"][-1]["status"] == agent_core.STATE_FAILED,
          "T3 普通 decide 看到失败结果，返回 finish 仍被步数闸门挡住")
    check(any("卡在还没做完的地方" in text
              for text, spoken in calls["notify"] if spoken),
          "T3 有失败步时仍如实报告未完成")
finally:
    pass


# 有 pending/结果未知/历史失败步时，步数满后的 finish 不能掩盖它们。
try:
    for status in (agent_core.STEP_PENDING, agent_core.STEP_STARTED,
                   agent_core.STEP_UNKNOWN, agent_core.STATE_FAILED):
        runner, calls = build(
            lambda context: {"kind": "finish", "say": "不应收尾", "llm_calls": 1},
            budget={"max_steps": 2})
        task = agent_core.new_task("未完成步不许收尾", [
            {"action": "remember", "args": {"n": 1}, "status": status},
            {"action": "remember", "args": {"n": 2}, "status": agent_core.STATE_DONE},
        ])
        task["budget_used"] = {"steps": 2, "llm_calls": 0, "seconds": 0.0}
        runner._run(task, 0)
        check(task.get("state") == agent_core.STATE_FAILED
              and task.get("stop_reason") == "budget_steps"
              and task["steps"][0]["status"] == status
              and not calls["perform"]
              and len(calls["decide"]) == (0 if status == agent_core.STEP_PENDING else 1),
              f"补充：存在 {status} 步时不得因末步成功就放行 finish，pending 在执行前拦")
finally:
    pass


# T4: 步数满后的收尾 decide 照常计入 LLM 调用，不虚报执行步数。
try:
    n = 2
    task, _, calls = run_dynamic(
        [next_step(i) for i in range(n)]
        + [{"kind": "finish", "say": "预算内正式收尾", "llm_calls": 1}],
        n, max_llm_calls=n + 1)
    check(task.get("state") == agent_core.STATE_DONE
          and task.get("stop_reason") == "finish"
          and task.get("budget_used", {}).get("llm_calls") == n + 1
          and len(calls["decide"]) == n + 1
          and calls["persist"][-1]["budget_used"]["llm_calls"] == n + 1,
          "T4 llm_calls 包含步数满后正常获准的那次收尾调用，且已落盘")
    check(task["budget_used"]["steps"] == n
          and len(task["steps"]) == n and len(calls["perform"]) == n
          and calls["decide"][-1]["budget"]["used_steps"] == n
          and calls["decide"][-1]["budget"]["remaining_llm_calls"] == 1,
          "T4 收尾只花 LLM 调用，不增加或虚报真实执行步数")
finally:
    pass


# T5: dynamic 关闭时不调用 decide，固定计划仍按预算失败。
try:
    calls = {"perform": [], "decide": []}

    def fixed_perform(step, task=None):
        calls["perform"].append(step.get("action"))
        return True, "固定计划完成", ""

    def fixed_decide(context):
        calls["decide"].append(context)
        return {"kind": "finish", "say": "不应调用", "llm_calls": 1}

    runner = agent_core.TaskRunner(
        perform=fixed_perform,
        persist=lambda task: None,
        notify=lambda text, spoken=False: None,
        decide=fixed_decide,
        sanitize=sanitize,
        dynamic=lambda: False,
        budget={"max_steps": 2},
    )
    task = agent_core.new_task("固定计划", [
        {"action": "remember", "args": {"n": 1}, "say": "第一步"},
        {"action": "remember", "args": {"n": 2}, "say": "第二步"},
    ])
    runner._run(task, 0)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_steps"
          and len(calls["perform"]) == 2
          and not calls["decide"],
          "T5 dynamic 关闭时打满步数仍 failed/budget_steps，不调用 decide")
finally:
    pass


print()
if fails:
    print(f"失败 {len(fails)} 条")
    for item in fails:
        print("  ", item)
    raise SystemExit(1)
print("全部通过 (预算收尾裁定 T1-T5)")
