# -*- coding: utf-8 -*-
"""视觉 v2 桩测: 两段式看屏 / 失败回读的视觉核实 / 敏感画面护栏。

不碰真实屏幕、不调真实模型、不落盘: pyautogui.screenshot / query_vision /
query_vision_locate / 前台窗口探测 / speak / 任务落盘 全部打桩。
"""
import base64
import io
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from PIL import Image   # noqa: E402
import pyautogui        # noqa: E402

import agent_core       # noqa: E402
import jarvis           # noqa: E402
import llm_brain        # noqa: E402

REAL = {
    "screenshot": pyautogui.screenshot,
    "qv": llm_brain.query_vision,
    "locate": llm_brain.query_vision_locate,
    "fg": jarvis._foreground_window_info,
    "identity": jarvis._foreground_window_identity,
    "speak": jarvis.speak,
    "perform": jarvis._perform_plan,
    "persist": jarvis._agent_persist,
    "notify": jarvis._agent_notify,
    "repair": jarvis._agent_repair_step,
}
BOX = {"x_min": 100, "y_min": 100, "x_max": 450, "y_max": 450}
LOCATE_JSON = ('```json\n' + '{"x_min":100,"y_min":100,"x_max":450,"y_max":450}' + '\n```')


def fake_screen():
    """1000x600 四色屏, 用中心像素颜色就能反推裁剪位置。"""
    img = Image.new("RGB", (1000, 600))
    px = img.load()
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    for y in range(600):
        for x in range(1000):
            px[x, y] = colors[(x >= 500) + 2 * (y >= 300)]
    return img


def decode(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def reset_confirm():
    jarvis._PEEK_CONFIRM.update(pending=False, question="", ts=0.0)


def main():
    fails = []
    jarvis.ui_bridge = None
    reset_confirm()
    pyautogui.screenshot = fake_screen
    jarvis._foreground_window_info = lambda: ("", "")
    jarvis.speak = lambda text: seen["spoken"].append(text)
    jarvis._agent_persist = lambda task: None
    jarvis._agent_notify = lambda text, spoken=False: None
    seen = {"qv": [], "loc": [], "spoken": []}

    def make_qv(*answers):
        calls = []
        seq = list(answers)

        def fake(question, b64, config, **kw):
            calls.append((question, b64))
            return seq.pop(0) if len(seq) > 1 else (seq[0] if seq else "")
        return fake, calls

    def install_qv(*answers):
        fake, calls = make_qv(*answers)
        llm_brain.query_vision = fake
        seen["qv"] = calls
        return calls

    # ═══ 一、两段式看屏 ═══
    # 1a. 触发+定位+裁剪+采用第二遍答案
    install_qv("第一遍: 看不清小字", "第二遍: ERROR 0x42")
    seen["loc"] = []
    llm_brain.query_vision_locate = lambda q, b, c, **kw: (seen["loc"].append((q, b)), LOCATE_JSON)[1]
    ans = jarvis._peek_screen("报错里写了什么？")
    ok = (ans == "第二遍: ERROR 0x42" and len(seen["qv"]) == 2
          and len(seen["loc"]) == 1 and seen["loc"][0][1] == seen["qv"][0][1])
    print(f"{'PASS' if ok else 'FAIL'}  细粒度问句触发第二遍并采用局部答案 → qv={len(seen['qv'])} ans={ans!r}")
    if not ok:
        fails.append(("两段式/触发与采用", ans, len(seen["qv"]), len(seen["loc"])))

    box = jarvis._peek_crop_box(BOX, 1000, 600)
    ok = box == (30, 18, 520, 312)
    print(f"{'PASS' if ok else 'FAIL'}  外扩20%+夹紧后像素框 → {box} (期望 (30,18,520,312))")
    if not ok:
        fails.append(("两段式/裁剪框", box))

    crop = decode(seen["qv"][1][1])
    mid = crop.getpixel((crop.size[0] // 2, crop.size[1] // 2))
    ok = crop.size == (490, 294) and mid[0] > 180 and mid[1] < 80 and mid[2] < 80
    print(f"{'PASS' if ok else 'FAIL'}  局部图从原始分辨率裁出 → 尺寸={crop.size} 中心={mid}")
    if not ok:
        fails.append(("两段式/裁剪尺寸", crop.size, mid))

    # 1b. 定位返回垃圾 → 回落第一遍, 不再问第二次
    install_qv("第一遍: 只有大字")
    seen["loc"] = []
    llm_brain.query_vision_locate = lambda q, b, c, **kw: (seen["loc"].append(1), "我不是JSON")[1]
    ans = jarvis._peek_screen("按钮上写了什么")
    ok = ans == "第一遍: 只有大字" and len(seen["qv"]) == 1 and len(seen["loc"]) == 1
    print(f"{'PASS' if ok else 'FAIL'}  定位垃圾回落第一遍 → qv={len(seen['qv'])} ans={ans!r}")
    if not ok:
        fails.append(("两段式/垃圾回落", ans, len(seen["qv"])))

    # 1c. 越界/整屏定位也要回落
    install_qv("第一遍: 整体描述")
    llm_brain.query_vision_locate = lambda q, b, c, **kw: '{"x_min":0,"y_min":0,"x_max":1000,"y_max":1000}'
    ans = jarvis._peek_screen("这个页面是什么")
    ok = ans == "第一遍: 整体描述" and len(seen["qv"]) == 1
    print(f"{'PASS' if ok else 'FAIL'}  整体性问题+越界坐标都不放大 → qv={len(seen['qv'])}")
    if not ok:
        fails.append(("两段式/整体性", ans, len(seen["qv"])))

    # 1c-2. F4: 面积阈值校验外扩+夹紧后的最终框，不是原始定位框
    huge = jarvis._peek_crop_box({"x_min": 50, "y_min": 60, "x_max": 950, "y_max": 940},
                                 1000, 600)
    ok = huge is None
    print(f"{'PASS' if ok else 'FAIL'}  F4 外扩后接近整屏的最终框放弃第二遍 → {huge}")
    if not ok:
        fails.append(("F4/最终框过大", huge))

    small = jarvis._peek_crop_box({"x_min": 500, "y_min": 485, "x_max": 515, "y_max": 515},
                                  2000, 1200)
    ok = small == (994, 574, 1036, 626)
    print(f"{'PASS' if ok else 'FAIL'}  F4 原始框偏小但外扩后达标则保留 → {small}")
    if not ok:
        fails.append(("F4/最终框达标", small))

    # 1d. 整体性问题不触发; 但第一遍说看不清就触发
    install_qv("第一遍: 一个桌面", "第二遍: 看出来了")
    seen["loc"] = []
    llm_brain.query_vision_locate = lambda q, b, c, **kw: (seen["loc"].append(1), LOCATE_JSON)[1]
    ans = jarvis._peek_screen("屏幕上是什么")
    ok = ans == "第一遍: 一个桌面" and not seen["loc"] and len(seen["qv"]) == 1
    print(f"{'PASS' if ok else 'FAIL'}  普通问题不触发第二遍 → loc={len(seen['loc'])}")
    if not ok:
        fails.append(("两段式/普通问题", ans, len(seen["loc"])))

    install_qv("第一遍: 看不清", "第二遍: ERROR 0x99")
    ans = jarvis._peek_screen("屏幕上是什么")
    ok = ans == "第二遍: ERROR 0x99" and len(seen["qv"]) == 2
    print(f"{'PASS' if ok else 'FAIL'}  第一遍'看不清'触发第二遍 → ans={ans!r}")
    if not ok:
        fails.append(("两段式/看不清触发", ans))

    # ═══ 二、失败回读的视觉核实 ═══
    UNVERIFIED = "open_app: 成功=无法核验；原因=仅尝试启动，未核实"

    def verify(answer=None, step=None, task=None, unverified=UNVERIFIED, fail=False):
        if answer is not None or answer is None and not fail:
            install_qv(answer or "看不清")
        if fail:
            def boom(q, b, c, **kw):
                raise RuntimeError("视觉服务挂了")
            llm_brain.query_vision = boom
        step = step or {"action": "open_app", "args": {"name": "记事本"},
                        "say": "记事本窗口应当打开", "scope": jarvis.AGENT_SCOPE_FREE}
        task = task if task is not None else {}
        return jarvis._agent_visual_verify(task, step, False, "启动失败", unverified), step, task

    # 2a. 三分支
    for answer, want in (("已出现", True), ("未出现", False), ("看不清", False)):
        (got_ok, _text, tool), st, _task = verify(answer)
        vv = st.get("vision_verify") or {}
        ok = (got_ok is want and vv.get("source") == "视觉核实"
              and vv.get("conclusion") == answer and "视觉核实=" + answer in tool
              and "预期效果是：记事本窗口应当打开" in vv.get("question", ""))
        print(f"{'PASS' if ok else 'FAIL'}  视觉核实 {answer} → ok={got_ok} 来源={vv.get('source')}")
        if not ok:
            fails.append(("视觉核实/" + answer, got_ok, vv, tool))

    # 2a-2. F2: 去空白/首尾标点后严格解析；不确定措辞优先；只认前缀
    verdict_cases = (
        ("已出现", "已出现"), ("  未出现。 ", "未出现"), ("已出现了！", "已出现"),
        ("已出现.", "已出现"),
        ("结论：已出现", "看不清"), ("可能已出现", "看不清"),
        ("已出现但不确定", "看不清"), ("没看清，似乎已出现", "看不清"),
        ("已出现，未出现", "已出现"), ("未出现，已出现", "未出现"),
        ("完全不知道", "看不清"), ("", "看不清"),
    )
    for answer, want in verdict_cases:
        got = jarvis._parse_vision_verdict(answer)
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  F2 裁决 {answer!r} → {got} (期望 {want})")
        if not ok:
            fails.append(("F2/裁决", answer, got, want))

    (got_ok, _text, tool), st, _task = verify(answer="可能已出现")
    ok = (got_ok is False and (st.get("vision_verify") or {}).get("conclusion") == "看不清"
          and "视觉核实=看不清" in tool)
    print(f"{'PASS' if ok else 'FAIL'}  F2 不确定答复不走'已出现'分支 → ok={got_ok}")
    if not ok:
        fails.append(("F2/集成不确定", got_ok, st.get("vision_verify"), tool))

    (got_ok, _text, tool), st, _task = verify(answer=" 未出现。")
    ok = got_ok is False and (st.get("vision_verify") or {}).get("conclusion") == "未出现"
    print(f"{'PASS' if ok else 'FAIL'}  F2 去空白/首尾标点后按前缀裁决 → ok={got_ok}")
    if not ok:
        fails.append(("F2/集成前缀", got_ok, st.get("vision_verify"), tool))

    # 2b. 调用失败 → 保持未核实
    (got_ok, _text, tool), st, _task = verify(fail=True)
    ok = got_ok is False and "调用失败" in tool and (st.get("vision_verify") or {}).get("conclusion") == "调用失败"
    print(f"{'PASS' if ok else 'FAIL'}  视觉调用异常 → 保持未核实 → {tool[-30:]}")
    if not ok:
        fails.append(("视觉核实/异常", got_ok, tool))

    # 2c. 单任务第 3 次跳过 (直接通过状态机跑 3 个未核实步)
    install_qv("已出现")
    calls = []
    jarvis._perform_plan = lambda plan: (
        setattr(jarvis._TL, "action_ok", False),
        setattr(jarvis._TL, "last_tool_text", UNVERIFIED),
        calls.append(plan))[2] or "启动失败"
    jarvis._agent_repair_step = lambda task, step, err: (None, "不修")
    task = {"goal": "三次核实", "state": "approved", "created": "v2桩测", "steps": [
        {"action": "open_app", "args": {"name": "A"}, "say": "A 打开", "status": "pending",
         "scope": jarvis.AGENT_SCOPE_FREE},
        {"action": "open_app", "args": {"name": "B"}, "say": "B 打开", "status": "pending",
         "scope": jarvis.AGENT_SCOPE_FREE},
        {"action": "open_app", "args": {"name": "C"}, "say": "C 打开", "status": "pending",
         "scope": jarvis.AGENT_SCOPE_FREE},
    ]}
    jarvis._agent_loop(task, 777)
    verdicts = [(s.get("vision_verify") or {}).get("conclusion") for s in task["steps"]]
    statuses = [s.get("status") for s in task["steps"]]
    ok = (len(seen["qv"]) == 2 and task.get("_vision_verify_count") == 2
          and verdicts == ["已出现", "已出现", "跳过"]
          and statuses == ["done", "done", "failed"])
    print(f"{'PASS' if ok else 'FAIL'}  单任务2次上限: qv={len(seen['qv'])} 结论={verdicts} 状态={statuses}")
    if not ok:
        fails.append(("视觉核实/次数上限", len(seen["qv"]), verdicts, statuses))

    # 2d. 危险步不走视觉核实, 也不占额度
    install_qv("已出现")
    danger = {"action": "close_app", "args": {"exe": "WeChat.exe"},
              "say": "微信应被关闭", "scope": jarvis.AGENT_SCOPE_ASK}
    task = {}
    (got_ok, _text, tool), st, task2 = verify(answer=None, step=danger, task=task)
    ok = (got_ok is False and not seen["qv"] and not task2
          and "跳过" in tool and "vision_verify" not in st)
    print(f"{'PASS' if ok else 'FAIL'}  危险步不核实 → qv={len(seen['qv'])} 状态不变")
    if not ok:
        fails.append(("视觉核实/危险步", got_ok, len(seen["qv"]), tool))

    # ═══ 三、敏感画面护栏 ═══
    # 3a. 用户 peek 命中 → 不发图, 先朗读提示, 记下窗口身份
    reset_confirm()
    install_qv("不该被调用")
    seen["spoken"] = []
    jarvis._foreground_window_info = lambda: ("微信 - 登录", "WeChat.exe")
    jarvis._foreground_window_identity = lambda: ("hwnd-a", "微信 - 登录")
    ans = jarvis._peek_screen("屏幕上写了什么")
    ok = (ans == jarvis._SENSITIVE_PROMPT and not seen["qv"]
          and jarvis._PEEK_CONFIRM["pending"]
          and jarvis._PEEK_CONFIRM["question"] == "屏幕上写了什么"
          and jarvis._PEEK_CONFIRM["window"] == ("hwnd-a", "微信 - 登录"))
    print(f"{'PASS' if ok else 'FAIL'}  敏感标题命中 → 不发图, 待确认并记下窗口 → {ans!r}")
    if not ok:
        fails.append(("敏感/peek提示", ans, jarvis._PEEK_CONFIRM["window"]))

    # 3b. F1: 肯定后复检仍敏感 → 不发图, 重走正常检查 (再次提示)
    rc = jarvis._peek_confirm_answer("确定")
    ok = (rc is True and not seen["qv"] and jarvis._PEEK_CONFIRM["pending"]
          and jarvis._PEEK_CONFIRM["window"] == ("hwnd-a", "微信 - 登录"))
    print(f"{'PASS' if ok else 'FAIL'}  F1 复检仍敏感 → 不发图, 重走检查 → qv={len(seen['qv'])}")
    if not ok:
        fails.append(("F1/复检敏感", rc, len(seen["qv"])))

    # 3c. F1: 肯定后前台窗口换了 (hwnd/标题都变) → 不发图, 重新提示并绑定新窗口
    jarvis._foreground_window_identity = lambda: ("hwnd-b", "支付页面 - 银行")
    jarvis._foreground_window_info = lambda: ("支付页面 - 银行", "chrome.exe")
    rc = jarvis._peek_confirm_answer("确定")
    ok = (rc is True and not seen["qv"] and jarvis._PEEK_CONFIRM["pending"]
          and jarvis._PEEK_CONFIRM["window"] == ("hwnd-b", "支付页面 - 银行"))
    print(f"{'PASS' if ok else 'FAIL'}  F1 确认后换窗口 → 不发图, 按新窗口重走检查 → qv={len(seen['qv'])}")
    if not ok:
        fails.append(("F1/窗口切换", rc, len(seen["qv"]), jarvis._PEEK_CONFIRM["window"]))

    # 3d. F1: 同一窗口复检不再敏感 → 才允许发图
    jarvis._foreground_window_identity = lambda: ("hwnd-a", "微信 - 登录")
    jarvis._foreground_window_info = lambda: ("支付页面 - 银行", "chrome.exe")
    reset_confirm()
    install_qv("确认后答案")
    ans = jarvis._peek_screen("现在是什么")
    jarvis._foreground_window_info = lambda: ("普通页面", "notepad.exe")
    seen["spoken"] = []
    rc = jarvis._peek_confirm_answer("确定")
    ok = (ans == jarvis._SENSITIVE_PROMPT and rc is True and len(seen["qv"]) == 1
          and not jarvis._PEEK_CONFIRM["pending"])
    print(f"{'PASS' if ok else 'FAIL'}  F1 同窗口复检不敏感 → 发图 → qv={len(seen['qv'])}")
    if not ok:
        fails.append(("F1/同窗口放行", rc, len(seen["qv"])))

    # 3e. F3: 只消费整句肯定/否定 (或 ≤4 字的纯确认短语)
    verdict_cases = (
        ("确定", "yes"), ("确定。！", "yes"), ("确定.", "yes"), ("嗯", "yes"), ("嗯嗯", "yes"),
        ("好的嗯", "yes"), ("确定确定", "yes"), ("确定确定哦", None),
        ("嗯打开浏览器", None), ("打开浏览器", None), ("不用", "no"),
        ("no", "no"), ("不", "no"), ("好不", None), ("", None),
    )
    for answer, want in verdict_cases:
        got = jarvis._peek_answer_verdict(answer)
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  F3 答复 {answer!r} → {got} (期望 {want})")
        if not ok:
            fails.append(("F3/答复判定", answer, got, want))

    jarvis._foreground_window_info = lambda: ("微信 - 登录", "WeChat.exe")
    reset_confirm()
    install_qv("不该被调用")
    seen["spoken"] = []
    ans = jarvis._peek_screen("屏幕上写了什么")
    rc = jarvis._peek_confirm_answer("嗯打开浏览器")
    ok = (ans == jarvis._SENSITIVE_PROMPT and rc is False and not seen["qv"]
          and not seen["spoken"] and not jarvis._PEEK_CONFIRM["pending"])
    print(f"{'PASS' if ok else 'FAIL'}  F3 非明确答复 → 清状态、返回 False、不吞指令 → rc={rc}")
    if not ok:
        fails.append(("F3/前缀指令", rc, len(seen["qv"]), seen["spoken"]))

    ans = jarvis._peek_screen("屏幕上写了什么")
    seen["spoken"] = []
    rc = jarvis._peek_confirm_answer("不用")
    ok = (rc is True and not seen["qv"] and not jarvis._PEEK_CONFIRM["pending"]
          and seen["spoken"] == ["好，那就不看屏了，Sir"])
    print(f"{'PASS' if ok else 'FAIL'}  F3 明确否定 → 取消看屏 → spoken={seen['spoken']}")
    if not ok:
        fails.append(("F3/明确否定", rc, seen["spoken"]))

    ans = jarvis._peek_screen("屏幕上写了什么")
    seen["spoken"] = []
    rc = jarvis._peek_confirm_answer("不用了")
    ok = (rc is False and not seen["qv"] and not seen["spoken"]
          and not jarvis._PEEK_CONFIRM["pending"])
    print(f"{'PASS' if ok else 'FAIL'}  F3 非整句否定 → 不消费 → rc={rc}")
    if not ok:
        fails.append(("F3/非整句否定", rc, seen["spoken"]))

    jarvis._peek_screen("屏幕上写了什么")
    with jarvis._PEEK_CONFIRM_LOCK:
        jarvis._PEEK_CONFIRM["ts"] = time.time() - jarvis._PEEK_CONFIRM_TTL - 1
    rc = jarvis._peek_confirm_answer("确定")
    ok = rc is False and not jarvis._PEEK_CONFIRM["pending"] and not seen["qv"]
    print(f"{'PASS' if ok else 'FAIL'}  F3 超时确认不再消费 → rc={rc}")
    if not ok:
        fails.append(("F3/超时", rc, len(seen["qv"])))

    # 3f. 自动核实命中 → 跳过且保持未核实
    jarvis._foreground_window_identity = REAL["identity"]
    install_qv("已出现")
    (got_ok, _text, tool), st, _task = verify()
    vv = st.get("vision_verify") or {}
    ok = (got_ok is False and not seen["qv"] and "敏感画面跳过" in tool
          and vv.get("reason") == "敏感画面跳过")
    print(f"{'PASS' if ok else 'FAIL'}  自动核实命中敏感 → 跳过且未核实 → {tool[-30:]}")
    if not ok:
        fails.append(("敏感/自动核实", got_ok, len(seen["qv"]), tool))

    print()
    if fails:
        print(f"失败 {len(fails)} 条")
        for f in fails:
            print("  ", f)
        return 1
    print("PASS: 两段式看屏/F1窗口绑定/F2严格裁决/F3明确答复/F4最终框面积 + 原有敏感护栏, 全程打桩")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        pyautogui.screenshot = REAL["screenshot"]
        llm_brain.query_vision = REAL["qv"]
        llm_brain.query_vision_locate = REAL["locate"]
        jarvis._foreground_window_info = REAL["fg"]
        jarvis._foreground_window_identity = REAL["identity"]
        jarvis.speak = REAL["speak"]
        jarvis._perform_plan = REAL["perform"]
        jarvis._agent_persist = REAL["persist"]
        jarvis._agent_notify = REAL["notify"]
        jarvis._agent_repair_step = REAL["repair"]
    sys.exit(rc)
