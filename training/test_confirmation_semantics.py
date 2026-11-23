# -*- coding: utf-8 -*-
"""通用确认语义、敏感窗口复用与动作回复回归。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jarvis

fails = []
def check(ok, message):
    if not ok:
        fails.append(message)

for phrase in ("确定了，大哥，赶快看呐", "确订", "缺定", "确D", "却定", "好，可以继续",
               "嗯嗯", "好的嗯", "确定确定", "确定确定哦", "我说确定"):
    check(jarvis.classify_confirmation_answer(phrase) == "confirm", f"{phrase!r} 应确认")
for phrase in ("不用了", "算了", "别看了", "取消", "不行", "我说过不要点"):
    check(jarvis.classify_confirmation_answer(phrase) == "deny", f"{phrase!r} 应拒绝")
for phrase in ("打开酷狗", "今天天气怎么样", "好不", "嗯打开浏览器", "我说点击发送会怎样"):
    check(jarvis.classify_confirmation_answer(phrase) == "none", f"{phrase!r} 不应确认")

def arm_pending():
    with jarvis._PEEK_CONFIRM_LOCK:
        jarvis._PEEK_CONFIRM.update(pending=True, question="屏幕上有什么", ts=time.time(),
                                    window=(1, "微信"), source="local", action="peek_screen")

# 跨来源回答不消费；非明确答复清状态、返回 False、不吞后续指令。
real_speak = jarvis.speak
spoken = []
jarvis.speak = spoken.append
try:
    arm_pending()
    check(not jarvis._peek_confirm_answer("确定", "remote"), "跨来源回答不消费确认")
    check(jarvis._PEEK_CONFIRM.get("pending"), "跨来源后仍保留确认")
    check(not jarvis._peek_confirm_answer("看一下屏幕", "local"), "重复看屏是非明确答复, 返回 False")
    check(not jarvis._PEEK_CONFIRM.get("pending"), "非明确答复应清状态")
    check(not spoken, "非明确答复不空回应、不吞后续指令")
finally:
    jarvis.speak = real_speak
    with jarvis._PEEK_CONFIRM_LOCK:
        jarvis._PEEK_CONFIRM.update(pending=False, question="", ts=0.0, window=(), source="local")

# 敏感窗口确认后，同一 hwnd+标题 5 分钟内再次看屏不再询问（缓存不免复检）。
real_sensitive = jarvis._sensitive_window_hit
real_identity = jarvis._foreground_window_identity
real_capture = jarvis._capture_screen_image
real_encode = jarvis._encode_jpeg_b64
import llm_brain
real_vision = llm_brain.query_vision
identity = (77, "微信 - 登录")
gate = {"sensitive": False, "checks": 0}

def fake_sensitive():
    gate["checks"] += 1
    return gate["sensitive"]

jarvis._sensitive_window_hit = fake_sensitive
jarvis._foreground_window_identity = lambda: identity
jarvis._capture_screen_image = lambda: object()
jarvis._encode_jpeg_b64 = lambda image: ("b64", 10)
llm_brain.query_vision = lambda *args, **kwargs: "屏幕结果"
jarvis._SENSITIVE_APPROVALS.clear()
try:
    check(jarvis._peek_screen("看内容", sensitive_confirmed=True, confirmed_window=identity,
                              approved_by_user=True) == "屏幕结果", "确认后复检不敏感应发图")
    gate["sensitive"] = True
    checks_before = gate["checks"]
    check(jarvis._peek_screen("再看内容") == "屏幕结果", "同一窗口再次看屏应免问")
    check(gate["checks"] > checks_before, "免问缓存不许绕过发图前复检")
    check(not jarvis._PEEK_CONFIRM.get("pending"), "免问时不应登记待确认")
finally:
    jarvis._sensitive_window_hit = real_sensitive
    jarvis._foreground_window_identity = real_identity
    jarvis._capture_screen_image = real_capture
    jarvis._encode_jpeg_b64 = real_encode
    llm_brain.query_vision = real_vision
    jarvis._SENSITIVE_APPROVALS.clear()

# 重复请求动作结果为最终回复；失败信息也直接来自动作返回值。
real_perform = jarvis._perform_plan
real_speak = jarvis.speak
heard = []
jarvis._perform_plan = lambda plan, source="": "桩动作结果：定位失败，未执行点击"
jarvis.speak = heard.append
try:
    jarvis.execute_plan({"action": "peek_screen", "args": {"question": "这是什么"}, "say": "我看看屏幕"})
    check(bool(heard) and "定位失败" in heard[-1], "动作失败原因应成为本轮回复")
finally:
    jarvis._perform_plan = real_perform
    jarvis.speak = real_speak

# 酷狗场景的礼貌动作请求仍进入动作计划，不落闲聊。
for utterance, expected in (("你能帮我点击我的收藏吗", "click_screen"),
                            ("您再确认一下屏幕", "peek_screen")):
    got = (jarvis.parse_command(utterance) or {}).get("action")
    check(got == expected, f"{utterance!r} 应路由 {expected}，实际 {got}")

# 当前标题记忆不是噪声称呼来源；下游提示明确禁止采纳转写名字。
from llm_brain import CHAT_SYSTEM_PROMPT
check("疑似人名一律当噪声" in CHAT_SYSTEM_PROMPT, "闲聊提示需拒绝 ASR 人名噪声")

if fails:
    print("FAIL")
    for failure in fails:
        print(" -", failure)
    raise SystemExit(1)
print("PASS: confirmation semantics / pending state / action response")
