# -*- coding: utf-8 -*-
"""真实语料路由考卷: 全打桩, 不联网/不启动进程/不碰真实记忆文件。"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis  # noqa: E402
import llm_brain  # noqa: E402
import memory_store  # noqa: E402

DOUBLE_JSON_2059 = (
    '{"action": "chat", "args": {}, "say": "先生，合肥今天天气不错"}'
    '{"action": "web_search", "args": {"query": "合肥天气"}, "say": "正在搜索"}'
)

fails = []


def check(condition, message):
    if not condition:
        fails.append(message)
        print("FAIL ", message)
    else:
        print("PASS ", message)


spoken = []
plans = []
low_spoken = []
saved = {
    "speak": jarvis.speak, "speak_low": jarvis._speak, "exec": jarvis.execute_plan,
    "plan": llm_brain.query_llm_plan, "chat": llm_brain.free_chat,
    "exit": jarvis.ON_EXIT, "ui": jarvis.ui_bridge, "multi": jarvis._multi_open_plans,
    "current": jarvis._command_is_current, "record": jarvis.MEMORY.record_turn,
    "last": jarvis.MEMORY.last_exchange, "hearing": jarvis.ON_HEAR,
}


def capture(text):
    spoken.append(text)


def fake_exec(plan, command_id=None):
    plans.append(plan)
    return False


def run(command):
    spoken.clear()
    plans.clear()
    jarvis._handle_command_inner(command, "stub")
    return list(spoken)


with tempfile.TemporaryDirectory() as tmp:
    memory_store.set_store(memory_store.MemoryStore(Path(tmp) / "corpus_store.json"))
    memory_store.SUGGESTION.asked = False
    memory_store.SUGGESTION.pending = ""
    memory_store.SUGGESTION.dismissed.clear()
    jarvis.QUIT_REQUESTED.clear()
    jarvis.speak = capture
    jarvis._speak = lambda text: low_spoken.append(text)
    jarvis.execute_plan = fake_exec
    llm_brain.query_llm_plan = lambda *a, **k: None
    llm_brain.free_chat = lambda *a, **k: ""
    jarvis.ON_EXIT = lambda: None
    jarvis.ui_bridge = None
    jarvis._multi_open_plans = lambda text: None
    jarvis._command_is_current = lambda cid: True
    jarvis.MEMORY.record_turn = lambda *a, **k: None
    jarvis.MEMORY.last_exchange = lambda: {}
    jarvis.ON_HEAR = None

    try:
        questions = ["你能同时打开两个应用吗", "算了，你能帮我同时打开两个应用吗",
                     "你还能查软件的运行进程"]
        for text in questions:
            check(jarvis.is_capability_question(text), f"能力询问护栏识别: {text}")
            plan = jarvis.parse_command(text)
            check((plan or {}).get("action") not in ("open_app", "search_file"),
                  f"疑问句不被本地规则抢答: {text}")
            run(text)
            check(not any((p or {}).get("action") in ("open_app", "search_file") for p in plans),
                  f"路由级疑问句未触发 open_app/search_file: {text}")

        for text in ["退下", "你可以退出了", "关闭自己"]:
            check(jarvis.is_self_exit(text), f"退出自身句式识别: {text}")
            out = run(text)
            check(any("休息" in line for line in out) and
                  not any("我还不知道怎么关闭" in line for line in out),
                  f"退出自身播报告别语而非关闭软件兜底: {text}")
        check(jarvis.QUIT_REQUESTED.is_set(), "退出走内部 quit 请求事件")
        check(not jarvis.is_self_exit("退出微信"), "关别的软件不误判为退出自身")
        check(not any((p or {}).get("action") == "close_app" for p in plans), "退出自身不产生 close_app")

        for text in ["屏幕中显示的是什么", "屏幕上显示什么", "屏幕里是什么", "我屏幕上是什么"]:
            plan = jarvis.parse_command(text)
            check((plan or {}).get("action") == "peek_screen", f"看屏首问直达 peek: {text}")
            run(text)
            check(bool(plans) and (plans[-1] or {}).get("action") == "peek_screen",
                  f"路由级看屏直达: {text}")

        corrected = jarvis.correct_asr_text("景德真的天气怎么样")
        check(corrected == "景德镇的天气怎么样", "景德真 → 景德镇 (天气语境)")
        check(jarvis.parse_command(corrected)["action"] == "web_search" and
              "景德镇" in jarvis.parse_command(corrected)["args"]["query"],
              "纠错后走天气/搜索且查询含景德镇")
        run(corrected)
        check(bool(plans) and (plans[-1] or {}).get("action") in ("web_search", "chat") and
              "景德镇" in str(plans[-1]), "路由级天气查询带纠错后的城市名")

        check(jarvis.correct_asr_text("和菲") == "和菲", "无上下文的和菲不纠 (门要严)")
        app_text = jarvis.correct_asr_text("打开兜包")
        check(app_text == "打开豆包", "兜包 → 豆包 (应用语境)")
        app_plan = jarvis.parse_command(app_text)
        check("豆包" in str((app_plan or {}).get("args") or {}) and "兜包" not in app_text,
              "纠错后应用路由瞄准豆包")
        tech = jarvis.correct_asr_text("本地大模型发展史里讲魔鞋和TrashForm")
        check("模型" in tech and "Transformer" in tech and "魔鞋" not in tech
              and "TrashForm" not in tech, "科技语境专名纠错 (魔鞋/TrashForm)")

        low_spoken.clear()
        jarvis.speak = saved["speak"]
        try:
            jarvis.speak(DOUBLE_JSON_2059)
        finally:
            jarvis.speak = capture
        check(low_spoken == [jarvis._JSON_SPEAK_FALLBACK], "20:59 双 JSON 原案整句替换为兜底句")

        low_spoken.clear()
        jarvis.speak = saved["speak"]
        try:
            jarvis.speak("好的，Sir，这就来")
        finally:
            jarvis.speak = capture
        check(low_spoken == ["好的，先生，这就来"], "speak 出口 Sir → 先生")

        store = memory_store.get_store()
        out = run("记住周会纪要放在文档里")
        check(any("记住了：周会纪要放在文档里" in line for line in out) and
              len(store.entries) == 1 and store.entries[0]["kind"] == "fact",
              "记住 → 写入 fact 并播报")
        run("记住以后每次开会提前十分钟")
        check(len(store.entries) == 2 and store.entries[1]["kind"] == "habit", "以后/每次 → habit")
        out = run("你记住了什么")
        check(any("2条长期记忆" in line for line in out) and "周会纪要" in out[-1],
              "列记忆 → 条数 + 最近条目摘要")
        out = run("忘掉周会纪要")
        check(any("已忘掉" in line for line in out) and len(store.entries) == 1,
              "忘掉 → 唯一命中直接删并播报")

        memory_store.SUGGESTION.asked = False
        memory_store.SUGGESTION.pending = ""
        jarvis.MEMORY.data["conversations"] = [
            {"role": "user", "text": "把周报存到桌面"}, {"role": "user", "text": "把周报存到桌面"}]
        before_suggest = len(store.entries)
        out = run("把周报存到桌面")
        check(any("说过几次" in line for line in out), "同一指令第 3 次才问是否记成习惯")
        check(len(store.entries) == before_suggest, "习惯建议只提问, 不擅自写")
        out = run("好，记住吧")
        check(len(store.entries) == before_suggest + 1 and
              store.entries[-1]["kind"] == "habit" and store.entries[-1]["source"] == "suggested",
              "用户肯定后写 habit")
        out = run("把周报存到桌面")
        check(not any("说过几次" in line for line in out), "同一会话不再重复问")

        out = run("不要叫我Sir，叫我老板")
        check(store.current_title() == "老板" and any("老板" in line for line in out)
              and any(item["text"] == "称呼=老板" for item in store.entries),
              "纠正句自动沉淀称呼并确认")
        run("以后叫我先生")
        titles = [item for item in store.entries if item["text"].startswith("称呼=")]
        check(len(titles) == 1 and store.current_title() == "先生", "同类称呼更新不新增")

        from llm_brain import sanitize_plan
        rem = sanitize_plan({"action": "remember", "args": {"text": "喜欢简短回答", "kind": "preference"}})
        check(rem["action"] == "remember" and rem["args"]["kind"] == "preference",
              "LLM 动作 remember 通过出口校验")
        forget = sanitize_plan({"action": "forget_memory", "args": {"keyword": "周报"}})
        check(forget["action"] == "forget_memory", "LLM 动作 forget_memory 通过出口校验")
        low_spoken.clear()
        jarvis.speak = saved["speak"]
        try:
            jarvis.speak(jarvis._perform_plan(rem))
        finally:
            jarvis.speak = capture
        check(any("记住了" in line for line in low_spoken) and
              any(item["text"] == "喜欢简短回答" for item in store.entries),
              "LLM remember 动作写入同一份记忆")
    finally:
        for key, value in saved.items():
            setattr(jarvis if key not in ("plan", "chat") else llm_brain,
                    {"plan": "query_llm_plan", "chat": "free_chat"}.get(key, key), value)

if fails:
    print(f"\n失败 {len(fails)} 项")
    for item in fails:
        print(" -", item)
    sys.exit(1)
print("\nPASS: 真实语料路由考卷全过")
