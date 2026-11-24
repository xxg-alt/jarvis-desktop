# -*- coding: utf-8 -*-
"""对话方式常驻考卷: 用户真机原话逐条判类别 + 三机制行为断言。

考卷只用可测层: 本地解析/路由分流 parse_command、确认判定器
classify_confirmation_answer、看屏 _peek_question_from_text、点击 _click_intent。
语料原句只作考题, 不作实现依据; 修前用 BASELINE 跑, 修后自动改走
jarvis.classify_dialogue_turn (同一套可测层 + 当前任务状态), 同一把尺子前后可比。
"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis  # noqa: E402
import llm_brain  # noqa: E402
import memory_store  # noqa: E402
from training.dialogue_corpus import CASES, SOURCE  # noqa: E402

fails = []


def check(condition, message):
    if not condition:
        fails.append(message)
        print("FAIL ", message)
    else:
        print("PASS ", message)


def _tight(text):
    import re
    return re.sub(r"[\s，,。！？!?：:；;、﹐﹑]", "", str(text or "")).lower()


def _pure_confirm(text):
    """确认判定器只在'整句就是答复'时才算数: 否则'识别'里的'别'也会误判。"""
    verdict = jarvis.classify_confirmation_answer(text)
    return verdict if verdict != "none" and len(_tight(text)) <= 12 else "none"


def baseline(text):
    """修前基线: 只用现有可测层, 无当前任务状态。"""
    plan = jarvis.parse_command(text) or {}
    action = str(plan.get("action") or "")
    if action:
        return "action:" + action if action != "chat" else "chitchat"
    if _pure_confirm(text) != "none":
        return "confirm"
    if jarvis._click_intent(text)[1] == "command":
        return "action:click_screen"
    if jarvis._peek_question_from_text(text):
        return "action:peek_screen"
    if jarvis._looks_conversational(text):
        return "chitchat"
    return "unknown"


def prime(case):
    ctx = case.get("context") or {}
    jarvis._PEEK_FOLLOW.update(
        armed=bool(ctx.get("peek_followup")),
        ts=time.time() if ctx.get("peek_followup") else 0.0,
    )
    reset = getattr(jarvis, "reset_dialogue_state", None)
    if reset:
        reset()
    set_task = getattr(jarvis, "set_current_task", None)
    if set_task and ctx.get("action"):
        set_task(action=ctx["action"], target=ctx.get("target", ""),
                 result=ctx.get("result", ""), status=ctx.get("status", "open"))


def classify(case):
    fn = getattr(jarvis, "classify_dialogue_turn", None)
    return fn(case["text"]) if fn else baseline(case["text"])


def main():
    print(f"语料来源: {SOURCE}  共 {len(CASES)} 条用户话语")
    hits = 0
    misses = []
    for case in CASES:
        prime(case)
        got = classify(case)
        ok = got == case["category"]
        hits += int(ok)
        if not ok:
            misses.append((case["id"], case["text"], case["category"], got))
        print(f"{'PASS' if ok else 'FAIL'} #{case['id']:2d} "
              f"{case['category']:<20s} got={got:<20s} | {case['text']}")
    rate = 100.0 * hits / len(CASES)
    print(f"\n命中率: {hits}/{len(CASES)} = {rate:.1f}%")
    if misses:
        print("未命中清单:")
        for mid, text, want, got in misses:
            print(f"  #{mid:2d} 期望={want} 实得={got} | {text}")

    check(hits == len(CASES), f"语料类别命中率 100% (当前 {rate:.1f}%)")

    # ---- 三机制行为断言 (修前 API 不存在, 自然计入失败) ----
    reset = getattr(jarvis, "reset_dialogue_state", None)
    set_task = getattr(jarvis, "set_current_task", None)
    followup = getattr(jarvis, "answer_followup", None)
    gate = getattr(jarvis, "capability_gate", None)
    note = getattr(jarvis, "note_current_task", None)
    check(all(fn is not None for fn in (reset, set_task, followup, gate, note)),
          "三机制 API 已落地 (reset/set_current_task/answer_followup/capability_gate/note_current_task)")

    if followup:
        reset()
        set_task(action="click_screen", target="我的收藏", status="unverified")
        reply = followup("点的没有") or ""
        check("点击" in reply and "我的收藏" in reply,
              f"短追问由上轮动作记录回答: {reply}")
        check("您要确认什么" not in reply and "没听清" not in reply,
              "短追问不落闲聊空谈/反问用户")
        reset()
        check("没有可追问" in (followup("哪个动作") or ""),
              "无上轮记录时如实说没有可追问的动作")

    if gate:
        plan = {"action": "chat", "say": "说明一下能力边界：我能读取你的文件、给出修改后的全文，也能直接写成新文档"}
        out = gate("做得很好,现在已经进去了", plan)
        check("能力边界" not in str(out.get("say", "")),
              "夸奖后能力话术被语境门挡掉")
        out2 = gate("你现在可以修改我文章里的内容了吧", plan)
        check("能力边界" in str(out2.get("say", "")),
              "直接问能力时能力话术照常放行")

    if note and reset:
        reset()
        set_task(action="click_screen", target="微信按钮", status="unverified")
        note("做得很好,现在已经进去了", {"action": "chat"}, "好，已记下")
        status = str(jarvis.current_task_snapshot().get("status", ""))
        check(status == "done", f"反馈收尾把当前任务标记完成 (status={status})")

    # ---- 全路径端到端: 确认机制真的接在指令入口上, 不是只挂在测试里 ----
    if not all((reset, set_task, followup, gate, note)):
        print("\n结果:", "全部通过" if not fails else f"{len(fails)} 项失败")
        return 1 if fails else 0

    spoken, plans = [], []
    saved = [
        (jarvis, "speak", jarvis.speak), (jarvis, "execute_plan", jarvis.execute_plan),
        (jarvis, "ui_bridge", jarvis.ui_bridge), (jarvis, "MEMORY", jarvis.MEMORY),
        (llm_brain, "query_llm_plan", llm_brain.query_llm_plan),
        (jarvis.MEMORY, "record_turn", jarvis.MEMORY.record_turn),
        (jarvis.MEMORY, "last_exchange", jarvis.MEMORY.last_exchange),
        (jarvis, "_command_is_current", jarvis._command_is_current),
        (jarvis, "_multi_open_plans", jarvis._multi_open_plans),
        (jarvis, "ON_HEAR", jarvis.ON_HEAR),
        (memory_store.SUGGESTION, "asked", memory_store.SUGGESTION.asked),
        (jarvis, "_verify_last_action", jarvis._verify_last_action),
        (memory_store, "_STORE", memory_store._STORE),
    ]

    def capture(text):
        spoken.append(text)

    def fake_exec(plan, command_id=None, source=""):
        plans.append(plan)
        if plan:
            spoken.append(str(plan.get("say") or ""))
        return False

    def run(command):
        spoken.clear()
        plans.clear()
        jarvis._handle_command_inner(command, "stub")
        return list(spoken)

    with tempfile.TemporaryDirectory() as tmp:
        memory_store.set_store(memory_store.MemoryStore(Path(tmp) / "dialogue_store.json"))
        memory_store.SUGGESTION.asked = True
        jarvis.speak = capture
        jarvis.execute_plan = fake_exec
        jarvis.ui_bridge = None
        jarvis.MEMORY.record_turn = lambda *a, **k: None
        jarvis.MEMORY.last_exchange = lambda: {}
        jarvis._command_is_current = lambda cid: True
        jarvis._multi_open_plans = lambda text: None
        jarvis.ON_HEAR = None
        jarvis._verify_last_action = lambda: None
        ability_say = ("说明一下能力边界：我能读取你的文件、给出修改后的全文，"
                       "也能直接写成新文档，但没法替你改动编辑器里已经打开的那篇")
        try:
            reset()
            set_task(action="open_app", target="微信", status="done")
            run("点击进入微信")
            check(bool(plans) and plans[-1].get("action") == "click_screen",
                  "意图持续: 打开后重复'进入'升级为 click_screen")
            check(any("还是这件事" in line for line in spoken),
                  "意图持续: 回复点明还是这件事, 不原样重开")

            reset()
            set_task(action="click_screen", target="微信按钮", status="unverified")
            run("点进录微信的按钮啊")
            check(bool(plans) and plans[-1].get("action") == "peek_screen",
                  "意图持续: 点击重复先核验当前状态, 不盲目连点")

            llm_brain.query_llm_plan = lambda *a, **k: {
                "action": "chat", "args": {}, "say": ability_say}
            reset()
            set_task(action="click_screen", target="微信按钮", status="unverified")
            out = run("做得很好,现在已经进去了")
            check(any("收到" in line for line in out) and not any("能力边界" in line for line in out),
                  f"反馈收尾: 全路径只短确认, 不背能力说明 {out}")
            check(not plans, "反馈收尾: 不进规划/执行通道")
            check(str(jarvis.current_task_snapshot().get("status")) == "done",
                  "反馈收尾: 当前任务标记完成")

            reset()
            set_task(action="click_screen", target="我的收藏", status="unverified")
            out = run("点的没有")
            check(any("我的收藏" in line for line in out) and not any("您要确认什么" in line for line in out),
                  f"短追问: 全路径由上一动作记录作答 {out}")
        finally:
            for obj, attr, value in saved:
                if (obj, attr) == (memory_store, "_STORE"):
                    if value is not None:
                        memory_store.set_store(value)
                else:
                    setattr(obj, attr, value)

    print("\n结果:", "全部通过" if not fails else f"{len(fails)} 项失败")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
