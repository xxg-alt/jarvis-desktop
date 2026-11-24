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
    return str((plan or {}).get("say") or "桩动作已完成")


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
    jarvis._TL.ref_state = {}


def run(text, source=""):
    spoken.clear()
    plans.clear()
    jarvis.handle_command(text, source=source)
    return list(spoken), [str(p.get("action") or "") for p in plans]


def run_real_exec(text, source=""):
    jarvis.execute_plan = saved[1][2]
    try:
        return run(text, source)
    finally:
        jarvis.execute_plan = fake_exec


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
        # ---- (1) 闲聊提到的设备只能解析, 不许当应用假装打开 ----
        reset()
        out, acts = run("我家的电视机坏了")
        check(not [a for a in acts if a != "chat"], f"闲聊轮不动手: {acts}")
        check(any(e["name"] == "电视机" and e["type"] == "device" for e in entities()),
              f"入口①: 闲聊轮提到的电视机进清单, 实际 {entities()}")
        out, acts = run("打开它")
        check("open_app" not in acts and acts == ["chat"],
              f"设备只解析不执行: {acts} / {plans}")
        check("电视机" in plans[0]["say"] and "没有控制电视机" in plans[0]["say"],
              f"点名实体并如实说没有控制能力: {plans[0].get('say')}")
        check(plans[0].get("reference", {}).get("name") == "电视机",
              "解析结果带实体来源, 护栏可核对")
        out, acts = run_real_exec("打开它")
        check(any("电视机" in line and "没有控制电视机" in line for line in out)
              and not [p for p in performed if p.get("action") != "chat"],
              f"真实执行路径也要如实答复且不产生动作: {out} / {performed}")

        # ---- app/file 照常执行 ----
        reset()
        run("我刚用微信聊了两句")
        out, acts = run("打开它")
        check(acts == ["open_app"] and plans[0]["args"].get("name") == "微信",
              f"应用类照常打开: {acts} / {plans}")
        reset()
        jarvis._add_entity("周报.md", "file")
        out, acts = run("打开它")
        check(acts == ["open_file"] and plans[0]["args"].get("name") == "周报.md",
              f"文件类照常打开: {acts} / {plans}")

        # ---- (2) 放宽歧义: 只提过没操作过的相邻两轮候选要反问 ----
        reset()
        run("我装了微信")
        run("又装了QQ")
        out, acts = run("打开它")
        check(not acts, f"相邻两轮都只提过: 反问时绝不先执行, 实际 {acts}")
        check(any("哪个" in line and "微信" in line and "qq" in line for line in out),
              f"相邻两轮候选一句话列出反问, 实际 {out}")
        out2, acts2 = run("第2条")
        check(acts2 == ["open_app"] and plans[0]["args"].get("name") == "qq",
              f"共用的'第N条'答复选中 qq, 实际 {acts2} / {plans}")

        # ---- 最近优先例外一: 刚执行过的动作目标 ----
        reset()
        run("我提了微信和QQ")
        out, acts = run("打开QQ")
        check(acts == ["open_app"] and any(e["name"] == "qq" and e["executed"]
              for e in entities()), f"执行过的目标带动作标记: {entities()}")
        out, acts = run("打开它")
        check(acts == ["open_app"] and plans[0]["args"].get("name") == "qq",
              f"刚执行过的目标最近优先, 实际 {plans}")

        # ---- 最近优先例外二: 紧邻上一轮提到, 更早候选已在两轮以外 ----
        reset()
        run("我装了微信")
        run("今天天气不错")
        run("又装了QQ")
        out, acts = run("打开它")
        check(acts == ["open_app"] and plans[0]["args"].get("name") == "qq",
              f"紧邻上一轮提到的目标最近优先, 实际 {acts} / {plans}")

        # ---- 入口③ + (3) 歧义选择消费后改写成明确命令, 点击闸门复查通过 ----
        reset()
        out, acts = run("看看屏幕上那个进入微信按钮是什么")
        check(acts and acts[-1] == "peek_screen", f"看屏轮走到 peek_screen: {acts}")
        check(any(e["name"] == "进入微信按钮" and e["type"] == "ui" for e in entities()),
              f"入口③: 看屏目标描述进清单, 实际 {entities()}")
        run("看看屏幕上那个返回按钮是什么")
        out, acts = run("点一下这个")
        check(not acts and any("哪个" in line and "进入微信按钮" in line
              and "返回按钮" in line for line in out),
              f"两个界面目标歧义反问且不先执行: {out} / {acts}")
        out2, acts2 = run_real_exec("第2条")
        check(getattr(jarvis._TL, "user_text", "") == "点一下返回按钮",
              f"选择消费后原话改写成含实体名的明确命令: {getattr(jarvis._TL, 'user_text', '')}")
        check(performed and performed[-1].get("action") == "click_screen"
              and performed[-1]["args"].get("target") == "返回按钮",
              f"点击闸门复查通过并成功点击: {performed}")
        check(not any("您明确说要点哪个按钮" in line for line in out2),
              f"点击没有被闸门拒绝: {out2}")
        reset()
        out, acts = run("看看屏幕上那个进入微信按钮是什么")
        out, acts = run("点一下这个")
        check(acts == ["click_screen"] and plans[0]["args"].get("target") == "进入微信按钮",
              f"单界面候选照常点选: {plans}")
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
        check(any(e["name"] == "自研工具" and e["type"] == "app" and e["executed"]
              for e in entities()),
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

        # ---- (5) 规划器的危险计划目标还是代词: 先解析再写进护栏话术 ----
        reset()
        run("我装了微信")
        guard_desc.clear()
        jarvis._guard_confirm = lambda desc, **kw: guard_desc.append(desc) or False
        llm_brain.query_llm_plan = (lambda command, config, memory_context="":
                                    {"action": "close_app", "args": {"exe": "", "name": "它"},
                                     "danger": True, "guard_desc": "关闭它", "say": "这就关掉它"})
        out, acts = run("结束掉它")
        check(acts == ["close_app"] and plans[0]["args"].get("name") == "微信"
              and "微信" in plans[0].get("guard_desc", ""),
              f"规划器危险计划的代词目标先本地解析并写入话术: {plans}")
        saved[1][2](dict(plans[0]), source="text")
        check(len(guard_desc) == 1 and "微信" in guard_desc[0] and not performed,
              f"打桩确认前拦住危险动作: {guard_desc} / {performed}")
        llm_brain.query_llm_plan = fake_plan
        jarvis._guard_confirm = saved[7][2]

        # ---- (4) 本地解析是唯一结构化结论; 未判定时规划目标须在清单内 ----
        reset()
        run("我装了微信")
        run("帮我弄一下那个东西")
        plan_ctx = [ctx for kind, ctx in contexts if kind == "plan"]
        check(bool(plan_ctx) and "【最近提到的东西】" in plan_ctx[-1]
              and "微信" in plan_ctx[-1],
              f"规划器提示加了最近实体上下文行, 实际 {plan_ctx}")
        check("【指代规则】" in llm_brain.SYSTEM_PROMPT
              and "唯一结构化结论" in llm_brain.SYSTEM_PROMPT,
              "规划器静态提示声明本地结论唯一")
        jarvis.resolve_reference("打开它", None, "text")
        ctx = jarvis._with_reference_context("记忆")
        check("代词已解析为 微信，规划沿用" in ctx,
              f"上下文行写明解析结果让规划沿用: {ctx}")
        wrong = {"action": "open_app", "args": {"name": "QQ"}, "say": ""}
        aligned = jarvis._align_plan_to_reference(wrong, "text")
        check(aligned["args"].get("name") == "微信"
              and aligned.get("reference", {}).get("name") == "微信",
              f"规划器目标与本地结论不一致时改回本地结论: {aligned}")
        reset()
        run("我装了微信")
        run("又装了QQ")
        out, acts = run("打开它")
        fabricated = {"action": "open_app", "args": {"name": "编造的应用"}, "say": ""}
        check(jarvis._align_plan_to_reference(fabricated, "text") is None
              and not acts and any("哪个" in line for line in spoken),
              f"本地歧义时规划器编造实体必须拒执: {spoken} / {acts}")
        reset()
        run("我装了微信")
        local_plan = jarvis.resolve_reference("打开它")["plan"]
        check(local_plan["args"].get("name") == "微信"
              and "微信" in jarvis._with_reference_context(""),
              f"本地直达与规划上下文指向同一实体: {local_plan}")
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
