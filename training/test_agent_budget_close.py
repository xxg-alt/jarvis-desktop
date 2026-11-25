# -*- coding: utf-8 -*-
"""收尾裁定回归：步数用尽时，已完成的动态任务仍有一次正式收尾机会。"""
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


# T1: N 次真实步骤打满步数后，closing decide 返回 finish。
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
          and calls["decide"][-1].get("closing_decide", {}).get("allowed_kinds")
          == ["finish", "ask"],
          "T1 只执行 N 步，最后一次 decide 明确是 finish/ask 收尾裁定")
finally:
    pass


# Closing through the LLM-call gate also accepts finish and ask normally.
try:
    task, _, calls = run_dynamic(
        [next_step(1), {"kind": "finish", "say": "调用预算下完成", "llm_calls": 1}],
        10, max_llm_calls=1)
    check(task.get("state") == agent_core.STATE_DONE
          and task.get("stop_reason") == "finish"
          and task.get("budget_used", {}).get("llm_calls") == 2,
          "补充：LLM 预算触发的收尾 finish 仍计入真实调用")

    task, _, calls = run_dynamic(
        [next_step(1), {"kind": "ask", "question": "还需要确认哪一项？",
                        "llm_calls": 1}],
        1)
    check(task.get("state") == agent_core.STATE_WAITING
          and task.get("pending_question") == "还需要确认哪一项？"
          and task.get("stop_reason") is None
          and calls["decide"][-1].get("closing_decide", {}).get("allowed_kinds")
          == ["finish", "ask"],
          "补充：收尾 ask 正常挂起等待用户")
finally:
    pass


# T2: closing decide 返回 next_step 时，执行层丢弃它并保留原预算失败。
try:
    n = 2
    closing = next_step("不该执行")
    task, _, calls = run_dynamic(
        [next_step(i) for i in range(n)] + [closing], n)
    check(task.get("state") == agent_core.STATE_FAILED
          and task.get("stop_reason") == "budget_steps"
          and len(calls["perform"]) == n,
          "T2 收尾裁定误派 next_step -> failed/budget_steps，且新步没有执行")
    check(len(task.get("steps", [])) == n
          and all(step.get("status") == agent_core.STATE_DONE
                  for step in task["steps"]),
          "T2 收尾裁定的 next_step 没有进入任务步骤账本")
finally:
    pass


# T3: 最后一步失败时不得用 closing finish 冒充完成。
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
          "T3 末步失败且步数打满 -> failed，不给 finish 收尾机会")
    check(len(calls["decide"]) == 1
          and "closing_decide" not in calls["decide"][0],
          "T3 失败步不触发收尾裁定 decide")
finally:
    pass


# T4: closing_decide_used 落盘后恢复，不能再次调用 decide；收尾调用照常记账。
try:
    persisted = []

    def save(task):
        persisted.append(copy.deepcopy(task))

    n = 1
    task, _, first_calls = run_dynamic(
        [next_step(1), next_step("收尾裁定不许执行")], 10, persist=save,
        max_llm_calls=1)
    restored = copy.deepcopy(persisted[-1])
    second_decides = []

    def should_not_decide(context):
        second_decides.append(context)
        return {"kind": "finish", "say": "不该再次调用", "llm_calls": 1}

    runner2, second_calls = build(
        should_not_decide, budget={"max_steps": 10, "max_llm_calls": 1})
    runner2._run(restored, 0)
    check(restored.get("closing_decide_used") is True
          and not second_decides
          and restored.get("state") == agent_core.STATE_FAILED
          and restored.get("stop_reason") == "budget_llm",
          "T4 恢复后再次预算关停不重复触发 closing decide")
    check(restored.get("budget_used", {}).get("llm_calls") == n + 1
          and len(first_calls["decide"]) == n + 1
          and not second_calls["decide"],
          "T4 llm_calls 包含收尾裁定那一次调用")
finally:
    pass


# T5: dynamic 关闭时不走 closing decide，固定计划仍按预算失败。
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
          "T5 dynamic 关闭时打满步数仍 failed/budget_steps，不调用 closing decide")
finally:
    pass


print()
if fails:
    print(f"失败 {len(fails)} 条")
    for item in fails:
        print("  ", item)
    raise SystemExit(1)
print("全部通过 (预算收尾裁定 T1-T5)")
