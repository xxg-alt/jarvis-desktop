# -*- coding: utf-8 -*-
"""指代理解端到端考卷 (全打桩): 最近实体清单 / 代词解析 / 歧义反问 / 规划器上下文。

只把 speak/execute_plan/模型/界面打桩, 原话照旧从 handle_command 进去,
断言的是"最后交给执行层的动作计划"和"反问话术", 不碰真屏幕、真模型、真鼠标。
"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis           # noqa: E402
import llm_brain        # noqa: E402
import memory_store     # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL ", msg)
    else:
        print("PASS ", msg)


spoken = []
plans = []
contexts = []
guard_desc = []
performed = []

saved = [
    (jarvis, "speak", jarvis.speak),
    (jarvis, "execute_plan", jarvis.execute_plan),
    (jarvis, "_perform_plan", jarvis._perform_plan),
    (jarvis, "ui_bridge", jarvis.ui_bridge),
    (jarvis, "_cut_audio", jarvis._cut_audio),
    (jarvis, "_agent_interrupt", jarvis._agent_interrupt),
    (jarvis, "_guard_answer", jarvis._guard_answer),
    (jarvis, "_guard_confirm", jarvis._guard_confirm),
    (jarvis, "_peek_confirm_answer", jarvis._peek_confirm_answer),
    (jarvis, "_command_is_current", jarvis._command_is_current),
    (jarvis, "_multi_open_plans", jarvis._multi_open_plans),
    (jarvis, "ON_HEAR", jarvis.ON_HEAR),
    (llm_brain, "query_llm_plan", llm_brain.query_llm_plan),
    (llm_brain, "free_chat", llm_brain.free_chat),
    (jarvis.MEMORY, "record_turn", jarvis.MEMORY.record_turn),
    (jarvis.MEMORY, "last_exchange", jarvis.MEMORY.last_exchange),
    (memory_store.SUGGESTION, "asked", memory_store.SUGGESTION.asked),
    (memory_store, "_STORE", memory_store._STORE),
]


def fake_speak(text):
    spoken.append(str(text or ""))


def fake_exec(plan, command_id=None, source=""):
    plans.append(dict(plan or {}))
    return False


def fake_perform(plan, source=""):
    performed.append(dict(plan or {}))
    return "桩动作已完成"


def fake_free_chat(command, config, memory_context=""):
    contexts.append(("chat", str(memory_context or "")))
    return "挺好的，Sir"


def fake_plan(command, config, memory_context=""):
    contexts.append(("plan", str(memory_context or "")))
    return {"action": "chat", "args": {}, "say": "占位回答"}


def reset():
    spoken.clear()
    plans.clear()
    contexts.clear()
    guard_desc.clear()
    performed.clear()
    jarvis.reset_dialogue_state()
    with jarvis._GUARD_LOCK:
        jarvis._GUARD.update(active=False, answer="", event=None)
    with jarvis._REF_LOCK:
        jarvis._REF_PENDING.update(active=False, chosen=None, candidates=[])


def run(text, source=""):
    spoken.clear()
    plans.clear()
    jarvis.handle_command(text, source=source)
    return list(spoken), [str(p.get("action") or "") for p in plans]


def entities():
    return jarvis.current_task_snapshot()["entities"]


with tempfile.TemporaryDirectory() as tmp:
    memory_store.set_store(memory_store.MemoryStore(Path(tmp) / "reference_store.json"))
    memory_store.SUGGESTION.asked = True
    jarvis.speak = fake_speak
    jarvis.execute_plan = fake_exec
    jarvis._perform_plan = fake_perform
    jarvis.ui_bridge = None
    jarvis._cut_audio = lambda: None
    jarvis._agent_interrupt = lambda command: False
    jarvis._guard_answer = lambda text, source="": False
    jarvis._peek_confirm_answer = lambda text, source="": False
    jarvis._command_is_current = lambda cid: True
    jarvis._multi_open_plans = lambda text: None
    jarvis.ON_HEAR = None
    jarvis.MEMORY.record_turn = lambda *a, **k: None
    jarvis.MEMORY.last_exchange = lambda: {}
    llm_brain.query_llm_plan = fake_plan
    llm_brain.free_chat = fake_free_chat
    try:
        # ---- 用户原例: 闲聊轮提到电视机, 下一轮"打开他" ----
        reset()
        out, acts = run("我刚买了台电视机")
        check(not [a for a in acts if a != "chat"], f"闲聊轮不动手: {acts}")
        check(any(e["name"] == "电视机" and e["type"] == "device" for e in entities()),
              f"入口①: 闲聊轮提到的电视机进清单, 实际 {entities()}")
        out, acts = run("打开他")
        check(acts == ["open_app"] and plans[0]["args"].get("name") == "电视机",
              f"用户原例: 打开他 → 对电视机执行打开, 实际 {acts} / {plans}")
        check(not any("哪个" in line for line in out), f"单候选不反问: {out}")
        check(plans[0].get("reference", {}).get("name") == "电视机",
              "解析结果带实体来源, 护栏可核对")

        # ---- 两实体分先后: 取最近的空调 ----
        reset()
        run("我刚买了台电视机")
        run("客厅新装了空调")
        out, acts = run("打开它")
        check(acts == ["open_app"] and plans[0]["args"].get("name") == "空调",
              f"先电视后空调: 打开它 → 最近的空调, 实际 {plans}")

        # ---- 几乎同时提到: 只问一句, 不先执行, 答复走共用选择机制 ----
        reset()
        run("我刚买了电视机和空调")
        out, acts = run("打开它")
        check(not acts, f"歧义: 反问时绝不先执行, 实际 {acts}")
        check(any("哪个" in line and "电视机" in line and "空调" in line for line in out),
              f"歧义: 一句话列出候选反问, 实际 {out}")
        out2, acts2 = run("第2条")
        check(acts2 == ["open_app"] and plans[0]["args"].get("name") == "空调",
              f"歧义: 共用的'第N条'答复选中空调, 实际 {acts2} / {plans}")

        # ---- 入口③: 看屏看过按钮后, "点一下这个" ----
        reset()
        out, acts = run("看看屏幕上那个进入微信按钮是什么")
        check(acts and acts[-1] == "peek_screen", f"看屏轮走到 peek_screen: {acts}")
        check(any(e["name"] == "进入微信按钮" and e["type"] == "ui" for e in entities()),
              f"入口③: 看屏目标描述进清单, 实际 {entities()}")
        out, acts = run("点一下这个")
        check(acts == ["click_screen"] and plans[0]["args"].get("target") == "进入微信按钮",
              f"点一下这个 → 点刚看过的按钮, 实际 {plans}")
        check("进入微信按钮" in str(getattr(jarvis._TL, "user_text", "")),
              "点击闸门拿到的是解析后的明确目标原话")

        # ---- 过期: 时间 TTL 与轮数 TTL ----
        reset()
        run("我刚买了台电视机")
        jarvis._CURRENT_TASK["entities"][0]["ts"] -= 400
        out, acts = run("打开它")
        check(not acts and any("哪个" in line for line in out),
              f"时间过期: 反问不执行, 实际 {out} / {acts}")
        reset()
        run("我刚买了台电视机")
        jarvis._CURRENT_TASK["turn"] += 10
        out, acts = run("打开它")
        check(not acts and any("哪个" in line for line in out),
              f"轮数过期: 反问不执行, 实际 {out} / {acts}")

        # ---- 毫无历史 ----
        reset()
        out, acts = run("打开它")
        check(not acts and any("你指的是哪个" in line for line in out),
              f"无历史: 如实问哪个, 不瞎猜, 实际 {out} / {acts}")

        # ---- 入口②: 执行过的动作目标写回 (与当前任务记录同一处) ----
        reset()
        jarvis.note_current_task("打开自研工具",
                                 {"action": "open_app", "args": {"name": "自研工具"}})
        check(any(e["name"] == "自研工具" and e["type"] == "app" for e in entities()),
              f"入口②: note_current_task 写回执行目标, 实际 {entities()}")
        check(jarvis.current_task_snapshot().get("target") == "自研工具",
              "当前任务记录与实体清单同源, 不是两套状态")

        # ---- 危险动作护栏: 话术报出解析出的实体名 ----
        reset()
        run("我刚用微信聊了两句")
        out, acts = run("把它关掉")
        check(acts == ["close_app"] and plans[0].get("danger")
              and plans[0].get("reference", {}).get("name") == "微信",
              f"关掉代词目标: 走关闭类护栏路径, 实际 {plans}")
        jarvis._guard_confirm = lambda desc, **kw: guard_desc.append(desc) or False
        saved[1][2](dict(plans[0]), source="text")
        check(len(guard_desc) == 1 and "微信" in guard_desc[0] and not performed,
              f"护栏话术含解析出的实体名且未确认不执行, 实际 {guard_desc} / {performed}")
        jarvis._guard_confirm = saved[7][2]

        # ---- 规划器通道: 上下文行 + 本地/规划同口径 ----
        reset()
        run("我刚买了台电视机")
        run("帮我弄一下那个东西")
        plan_ctx = [ctx for kind, ctx in contexts if kind == "plan"]
        check(bool(plan_ctx) and "【最近提到的东西】" in plan_ctx[-1]
              and "电视机" in plan_ctx[-1],
              f"规划器提示加了最近实体上下文行, 实际 {plan_ctx}")
        check("【指代规则】" in llm_brain.SYSTEM_PROMPT,
              "规划器静态提示声明按清单解析代词")
        local_plan = jarvis.resolve_reference("打开它")["plan"]
        check(local_plan["args"].get("name") in plan_ctx[-1],
              "本地直达与规划上下文指向同一实体")
    finally:
        for obj, attr, value in saved:
            if (obj, attr) == (memory_store, "_STORE"):
                if value is not None:
                    memory_store.set_store(value)
            else:
                setattr(obj, attr, value)

if fails:
    print(f"\n结果: {len(fails)} 项失败")
    sys.exit(1)
print("\n结果: 指代理解考卷全部通过")
