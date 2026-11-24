# -*- coding: utf-8 -*-
"""独立反例集: 只用 handle_command 端到端打桩断言行为, 不用 classify_dialogue_turn 自打分。"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis  # noqa: E402
import llm_brain  # noqa: E402
import memory_store  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL ", msg)
    else:
        print("PASS ", msg)


spoken = []
plans = []
plan_sources = []
peek_calls = []

saved = [
    (jarvis, "speak", jarvis.speak),
    (jarvis, "execute_plan", jarvis.execute_plan),
    (jarvis, "_peek_screen", jarvis._peek_screen),
    (jarvis, "ui_bridge", jarvis.ui_bridge),
    (jarvis, "MEMORY", jarvis.MEMORY),
    (jarvis, "_cut_audio", jarvis._cut_audio),
    (jarvis, "_agent_interrupt", jarvis._agent_interrupt),
    (jarvis, "_guard_answer", jarvis._guard_answer),
    (jarvis, "_peek_confirm_answer", jarvis._peek_confirm_answer),
    (jarvis, "_command_is_current", jarvis._command_is_current),
    (jarvis, "_multi_open_plans", jarvis._multi_open_plans),
    (jarvis, "ON_HEAR", jarvis.ON_HEAR),
    (llm_brain, "query_llm_plan", llm_brain.query_llm_plan),
    (jarvis.MEMORY, "record_turn", jarvis.MEMORY.record_turn),
    (jarvis.MEMORY, "last_exchange", jarvis.MEMORY.last_exchange),
    (memory_store.SUGGESTION, "asked", memory_store.SUGGESTION.asked),
    (memory_store, "_STORE", memory_store._STORE),
]


def capture(text):
    spoken.append(str(text or ""))


def fake_exec(plan, command_id=None, source=""):
    plans.append(dict(plan or {}))
    plan_sources.append(jarvis._source_label(source))
    return False


def fake_peek(*args, **kwargs):
    peek_calls.append({"question": kwargs.get("question") or (args[0] if args else ""),
                       "source": jarvis._source_label(kwargs.get("source"))})
    return "核验完成"


def run(command, source=""):
    spoken.clear()
    plans.clear()
    plan_sources.clear()
    peek_calls.clear()
    jarvis.handle_command(command, source=source)
    return list(spoken), [str(p.get("action") or "") for p in plans]


def prime(action="", target="", status="open", source="", age=0.0):
    jarvis.reset_dialogue_state()
    if action:
        jarvis.set_current_task(action=action, target=target, status=status,
                                source=source, ts=time.time() - age)


with tempfile.TemporaryDirectory() as tmp:
    memory_store.set_store(memory_store.MemoryStore(Path(tmp) / "counterexample_store.json"))
    memory_store.SUGGESTION.asked = True
    jarvis.speak = capture
    jarvis.execute_plan = fake_exec
    jarvis._peek_screen = fake_peek
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
    llm_brain.query_llm_plan = lambda *a, **k: {
        "action": "chat", "args": {}, "say": "LLM兜底不应出现"}
    try:
        prime("click_screen", "微信按钮", "unverified", "remote")
        out, acts = run("已经进去了，你再看看里面有什么", "voice")
        check("peek_screen" in acts,
              f"反馈+同句请求: 剥离反馈后仍走看屏路由, 实际 {acts}")
        check("收到" not in "".join(out),
              f"反馈+同句请求: 不得只短确认就收尾, 实际 {out}")
        check(str(jarvis.current_task_snapshot().get("action")) == "peek_screen",
              "反馈+同句请求: 新请求写回当前任务, 不把整句当反馈")

        prime("click_screen", "微信按钮", "unverified", "remote")
        out, acts = run("做得很好,现在已经进去了", "voice")
        check(not acts and any("收到" in line for line in out),
              f"纯反馈: 仍只短确认收尾, 实际 {out} / {acts}")
        check(str(jarvis.current_task_snapshot().get("status")) == "done",
              "纯反馈: 当前任务标记完成")

        prime("open_app", "微信", "done")
        out, acts = run("打开微信", "remote")
        check(acts == ["open_app"] and plan_sources == ["remote"],
              f"重开不升级: 再说打开微信仍是 open_app, 实际 {acts} / {plan_sources}")
        check(not any("还是这件事" in line for line in out),
              "重开不升级: 不点明还是这件事, 不改成 click_screen")
        check(str(jarvis.current_task_snapshot().get("source")) == "remote",
              "任务记录: note_current_task 保存来源")

        out, acts = run("帮我点击进入", "remote")
        check(acts and acts[-1] == "click_screen",
              f"交叉用例: 打开后再说点击进入才走 click, 实际 {acts}")

        prime("open_app", "微信", "done")
        out, acts = run("重开微信", "remote")
        check(acts == ["open_app"], f"重开不升级: 明确重开同一应用仍 open_app, 实际 {acts}")

        prime("click_screen", "微信按钮", "unverified", "remote", age=181)
        out, acts = run("点的没有", "voice")
        check(any("没有可追问" in line for line in out),
              f"过期记录: 追问不得拿旧记录作答, 实际 {out}")
        check(not any("微信按钮" in line for line in out) and not peek_calls,
              "过期记录: 不泄露旧目标, 也不触发旧记录核验")

        prime("click_screen", "我的收藏", "unverified", "remote")
        out, acts = run("点的没有", "voice")
        check(len(peek_calls) == 1 and peek_calls[0]["source"] == "remote",
              f"跨来源核验: 核验按记录来源 remote 执行, 实际 {peek_calls}")
        check(any("我的收藏" in line for line in out) and not any("您要确认什么" in line for line in out),
              f"跨来源读取: 手机来源追问可读语音侧记录并作答, 实际 {out}")
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
print("\n结果: 独立反例集全部通过")
