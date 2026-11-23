# -*- coding: utf-8 -*-
"""待确认跨来源消费回归: 危险动作/敏感看屏只认发起来源的确认。"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis
import llm_brain

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def wait_until(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def src(value):
    return jarvis._source_label(value)


spoken = []
passed = []
executed = []
vision_calls = []

originals = {
    name: getattr(jarvis, name) for name in (
        "speak", "ui_bridge", "_RUN_LOCK", "_handle_command_inner", "_guard_confirm",
        "_sensitive_window_hit", "_foreground_window_identity",
        "_capture_screen_image", "_encode_jpeg_b64", "_peek_needs_second_pass",
    )
}
original_query_vision = llm_brain.query_vision


class NoRunLock:
    """打桩: 守卫测试里确认语必须能立刻走完正常流程, 不被业务锁等超时。"""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_speak(text):
    spoken.append(text)


def fake_inner(command, command_id, source=""):
    passed.append((command, src(source)))
    if command == "关机":
        jarvis.execute_plan({"action": "shutdown", "danger": True, "guard_desc": "关机"},
                            command_id=command_id, source=source)
    elif command == "看屏":
        jarvis.execute_plan({"action": "peek_screen",
                             "args": {"question": "屏幕上写了什么"}},
                            command_id=command_id, source=source)


original_perform_plan = jarvis._perform_plan


def fake_perform_plan(plan, source=""):
    if plan.get("action") == "shutdown":
        executed.append((plan.get("action"), src(source)))
        return "已记录危险动作"
    return original_perform_plan(plan, source=source)


def fast_guard_confirm(desc, timeout=jarvis.GUARD_TTL, source=""):
    return originals["_guard_confirm"](desc, timeout=2.0, source=source)


def fake_query_vision(question, b64, config, **kwargs):
    vision_calls.append(question)
    return "屏幕答案"


jarvis.speak = fake_speak
jarvis.ui_bridge = None
jarvis._RUN_LOCK = NoRunLock()
jarvis._handle_command_inner = fake_inner
jarvis._guard_confirm = fast_guard_confirm
jarvis._perform_plan = fake_perform_plan
jarvis._sensitive_window_hit = lambda: True
jarvis._foreground_window_identity = lambda: ("hwnd-a", "微信 - 登录")
jarvis._capture_screen_image = lambda: object()
jarvis._encode_jpeg_b64 = lambda img: ("b64", 100)
jarvis._peek_needs_second_pass = lambda question, first: False
llm_brain.query_vision = fake_query_vision


def reset():
    spoken.clear()
    passed.clear()
    executed.clear()
    vision_calls.clear()
    with jarvis._GUARD_LOCK:
        jarvis._GUARD.update(active=False, source="local", answer="", event=None)
    with jarvis._PEEK_CONFIRM_LOCK:
        jarvis._PEEK_CONFIRM.update(pending=False, question="", ts=0.0, window=(), source="local")
    jarvis._SENSITIVE_APPROVALS.clear()
    jarvis._sensitive_window_hit = lambda: True


def guard_case(name, pending_source, answer_source, answer_text, approved, followup=None):
    reset()
    thread = threading.Thread(target=jarvis.handle_command,
                              args=("关机", pending_source), daemon=True)
    thread.start()
    check(wait_until(lambda: jarvis._GUARD.get("active")
                     and src(jarvis._GUARD.get("source")) == src(pending_source)),
          f"{name}: 危险动作应登记来源为 {src(pending_source)} 的待确认")
    jarvis.handle_command(answer_text, source=answer_source)
    if not approved:
        check(jarvis._GUARD.get("active"), f"{name}: 来源不一致不得取消待确认")
    check(src(jarvis._GUARD.get("source")) == src(pending_source),
          f"{name}: 来源不一致不得改写待确认来源")
    if followup:
        jarvis.handle_command("确定", source=followup)
        check(wait_until(lambda: bool(executed)), f"{name}: 原来源确认后守卫应完成")
        check(len(executed) == 1 and executed[0][0] == "shutdown",
              f"{name}: 原来源随后确认仍应批准, 实际 {executed}")
    thread.join(timeout=4)
    check(not thread.is_alive(), f"{name}: 守卫线程应在超时内结束")
    final_approved = approved or bool(followup)
    check(bool(executed) == final_approved, f"{name}: 动作执行位应为 {final_approved}, 实际 {executed}")
    confirm_passed = any(text == answer_text for text, _ in passed)
    check(confirm_passed != approved, f"{name}: 首次确认语是否走正常流程应为 {not approved}")


def peek_case(name, pending_source, answer_source, answer_text, approved, followup=None):
    reset()
    jarvis.handle_command("看屏", source=pending_source)
    check(jarvis._PEEK_CONFIRM.get("pending")
          and src(jarvis._PEEK_CONFIRM.get("source")) == src(pending_source),
          f"{name}: 敏感看屏应登记来源为 {src(pending_source)} 的待确认")
    jarvis._sensitive_window_hit = lambda: False
    jarvis.handle_command(answer_text, source=answer_source)
    if not approved:
        check(jarvis._PEEK_CONFIRM.get("pending")
              and src(jarvis._PEEK_CONFIRM.get("source")) == src(pending_source),
              f"{name}: 来源不一致不得取消待确认")
    check(len(vision_calls) == (1 if approved else 0),
          f"{name}: 发图次数应为 {1 if approved else 0}, 实际 {vision_calls}")
    confirm_passed = any(text == answer_text for text, _ in passed)
    check(confirm_passed != approved, f"{name}: 确认语是否走正常流程应为 {not approved}")
    if followup:
        jarvis.handle_command("确定", source=followup)
        check(len(vision_calls) == 1, f"{name}: 原来源随后确认仍应批准, 实际 {vision_calls}")


try:
    guard_case("guard 本地待确认+远程确定", "voice", "remote", "确定", False)
    guard_case("guard 远程待确认+远程确定", "remote", "remote", "确定", True)
    guard_case("guard 本地待确认+本地确定", "voice", "voice", "确定", True)
    guard_case("guard 默认 local 待确认+默认 local 确定", "", "", "确定", True)
    guard_case("guard 远程待确认+本地无关指令", "remote", "text", "打开浏览器", False,
               followup="remote")

    peek_case("peek 本地待确认+远程确定", "voice", "remote", "确定", False)
    peek_case("peek 远程待确认+远程确定", "remote", "remote", "确定", True)
    peek_case("peek 本地待确认+本地确定", "voice", "voice", "确定", True)
    peek_case("peek 默认 local 待确认+默认 local 确定", "", "", "确定", True)
    peek_case("peek 远程待确认+本地无关指令", "remote", "text", "打开浏览器", False,
              followup="remote")
finally:
    for name, value in originals.items():
        setattr(jarvis, name, value)
    jarvis._perform_plan = original_perform_plan
    llm_brain.query_vision = original_query_vision

if fails:
    print("FAIL")
    for item in fails:
        print(" -", item)
    sys.exit(1)
print("PASS: 待确认来源隔离 10 项回归全过")
