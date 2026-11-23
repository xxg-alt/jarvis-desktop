# -*- coding: utf-8 -*-
"""看屏点击 click_screen 回归: 假截图/假定位/假点击记录器, 不碰真屏幕、真模型、真鼠标。"""
import sys
import time
from pathlib import Path

from PIL import Image   # noqa: E402
import pyautogui        # noqa: E402

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis           # noqa: E402
import llm_brain        # noqa: E402

REAL = {
    "screenshot": pyautogui.screenshot,
    "click": pyautogui.click,
    "locate": llm_brain.query_vision_locate,
    "vision": llm_brain.query_vision,
    "sensitive": jarvis._sensitive_window_hit,
    "identity": jarvis._foreground_window_identity,
    "guard": jarvis._guard_confirm,
    "peak_delay": jarvis._CLICK_VERIFY_DELAY,
    "speak": jarvis.speak,
    "ui": jarvis.ui_bridge,
}
SCREEN = (800, 600)
BOX = {"x_min": 100, "y_min": 200, "x_max": 300, "y_max": 400}
LOCATE_JSON = ('```json\n{"x_min":100,"y_min":200,"x_max":300,"y_max":400}\n```')

seen = {"shots": 0, "clicks": [], "locate": [], "vision": [], "guard": [], "spoken": []}
answer = {"locate": LOCATE_JSON, "label": "进入微信", "vision": "已完成", "guard": True,
          "raise_locate": False}


def fake_screen(*a, **kw):
    seen["shots"] += 1
    return Image.new("RGB", SCREEN, (18, 24, 36))


def fake_click(x=None, y=None, *a, **kw):
    seen["clicks"].append((x, y))


def fake_locate(question, b64, config, **kw):
    seen["locate"].append((question, b64))
    if answer["raise_locate"]:
        raise RuntimeError("定位服务挂了")
    return answer["locate"]


def fake_vision(question, b64, config, **kw):
    seen["vision"].append((question, b64))
    if question == jarvis._CLICK_LABEL_Q:
        return answer["label"]
    return answer["vision"]


def fake_guard(desc, **kw):
    seen["guard"].append(desc)
    return answer["guard"]


def reset(locate=None, label=None, vision=None, guard=True):
    for key in ("clicks", "locate", "vision", "guard", "spoken"):
        seen[key].clear()
    seen["shots"] = 0
    answer["locate"] = LOCATE_JSON if locate is None else locate
    answer["label"] = "进入微信" if label is None else label
    answer["vision"] = "已完成" if vision is None else vision
    answer["guard"] = guard
    answer["raise_locate"] = False
    jarvis._CLICK_VERIFY_DELAY = 0
    jarvis.ui_bridge = None
    jarvis._foreground_window_identity = lambda: ("hwnd-1", "微信")
    jarvis._sensitive_window_hit = lambda: False
    jarvis._PEEK_CONFIRM.update(pending=False, question="", ts=0.0, window=(),
                                source="local", action="peek_screen", target="",
                                click_confirmed=False)


def main():
    fails = []

    def check(ok, message):
        print(f"{'PASS' if ok else 'FAIL'}  {message}")
        if not ok:
            fails.append(message)

    pyautogui.screenshot = fake_screen
    pyautogui.click = fake_click
    llm_brain.query_vision_locate = fake_locate
    llm_brain.query_vision = fake_vision
    jarvis._sensitive_window_hit = lambda: False
    jarvis._foreground_window_identity = lambda: ("hwnd-1", "微信")
    jarvis._guard_confirm = fake_guard
    jarvis.speak = lambda text: seen["spoken"].append(text)
    jarvis.ui_bridge = None
    try:
        reset()
        out = jarvis._click_screen("进入微信按钮")
        check(seen["clicks"] == [(160, 180)], "正常定位: 只点框中心一点 (160,180)")
        check(seen["shots"] == 2, "点击前后各截一张屏 (共2张)")
        label_calls = [q for q, _b in seen["vision"] if q == jarvis._CLICK_LABEL_Q]
        verify_calls = [q for q, _b in seen["vision"] if q != jarvis._CLICK_LABEL_Q]
        check(len(label_calls) == 1 and len(verify_calls) == 1
              and "已完成/未完成/无法判断" in verify_calls[0],
              "点前裁框读按钮文字, 点后核验只给三选一裁决")
        label_b64 = [b for q, b in seen["vision"] if q == jarvis._CLICK_LABEL_Q][0]
        check(label_b64 != seen["locate"][0][1], "读按钮文字用的是裁出的局部图, 不是整屏图")
        check("已点击" in out and "已完成" in out and not seen["guard"],
              "明确已完成才播报成功, 安全目标不多问")

        reset()
        out = jarvis._perform_plan({"action": "click_screen", "args": {"target": "进入微信按钮"}})
        check(len(seen["clicks"]) == 1 and "已完成" in out, "动作分发 click_screen 走通且只点一次")

        reset(guard=False, label="确认订单")
        out = jarvis._click_screen("确认订单按钮")
        check(not seen["clicks"] and len(seen["guard"]) == 1,
              "危险按钮先二次确认, 未确认不点")
        check("确认订单" in seen["guard"][0] and "按钮上实际写着" in seen["guard"][0],
              "二次确认话术报出裁框读到的实际按钮文字")
        check("不点" in out, "拒绝确认后如实播报取消")

        reset(guard=True, label="确认订单")
        jarvis._click_screen("确认订单按钮")
        check(len(seen["clicks"]) == 1 and len(seen["guard"]) == 1,
              "危险按钮确认后只点一次, 不重复确认")

        reset(locate='{"x_min":100,"y_min":200,"x_max":300,"y_max":400,"label":"付款"}',
              label="付款", guard=False)
        jarvis._click_screen("付款按钮")
        check(not seen["clicks"] and len(seen["guard"]) == 1 and "付款" in seen["guard"][0],
              "定位输出与按钮原文都过危险词护栏, 话术报出实际文字")

        reset(label="看不清")
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and not seen["guard"] and "没看清按钮上的文字" in out,
              "按钮文字读不出: 拒绝点击并如实说")

        reset(label="关于我们")
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and "对不上" in out and "关于我们" in out,
              "按钮原文与目标核对不上: 拒绝点击并报出实际文字")

        reset(label="删除账号", guard=True)
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and len(seen["guard"]) == 1 and "删除账号" in seen["guard"][0],
              "危险原文即使与目标不符也先确认, 话术报出实际文字")
        check("对不上" in out and "删除账号" in out, "确认后仍核对不上就不点")

        for verdict in ("未完成", "无法判断", "已出现", "已完成，可能", ""):
            reset(vision=verdict)
            out = jarvis._click_screen("进入微信按钮")
            check(len(seen["clicks"]) == 1 and "点击已执行，结果未能确认" in out
                  and "已完成" not in out,
                  f"核验非明确已完成 → 统一未确认话术: {verdict or '空答复'}")

        for verdict in ("结论：已完成", "已完成。"):
            reset(vision=verdict)
            out = jarvis._click_screen("进入微信按钮")
            check("已点击" in out and "已完成" in out, f"严格解析仍认明确已完成: {verdict}")

        reset(locate="null")
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and not seen["vision"] and "没找到那个按钮" in out,
              "定位失败: 说没找到且绝不点击")

        reset(locate='{"x_min":-1,"y_min":200,"x_max":300,"y_max":400}')
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and "没找到那个按钮" in out, "坐标越界/非法: 不点")

        reset()
        answer["raise_locate"] = True
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and "没找到那个按钮" in out, "定位服务异常: 不猜坐标不乱点")

        check(jarvis._clamp_click_point(-7, 9999, 800, 600) == (0, 599)
              and jarvis._clamp_click_point(805, -3, 800, 600) == (799, 0),
              "点击坐标按屏幕边界夹紧")
        check(jarvis._click_point_from_box(BOX, 800, 600) == (160, 180)
              and jarvis._click_point_from_box({"x_min": 1}, 800, 600) is None,
              "框中心落在定位框内, 坏坐标返回 None")
        check(jarvis._parse_click_verdict("已完成") == "已完成"
              and jarvis._parse_click_verdict("未完成") == "未完成"
              and jarvis._parse_click_verdict("看不清") == "无法判断"
              and jarvis._parse_click_verdict("已完成也许") == "无法判断",
              "三选一裁决严格解析, 含糊词不判成功")

        plans = [(text, (jarvis.parse_command(text) or {}).get("action")) for text in (
            "打开微信", "点击进入微信", "点击", "按一下回车",
            "你能点击进入微信吗", "不要点击那个按钮", "不要点那个按钮", "别点那个按钮",
            "我说点击发送会怎样", "如果点了删除会", "如果点了删除会怎样", "如果点击删除会怎样",
            "「点击发送」这四个字是什么意思")]
        check(plans[0][1] == "open_app" and plans[1][1] == "click_screen"
              and "微信" in str(jarvis.parse_command("点击进入微信")["args"]),
              "点击进入微信走 click_screen, 不重开软件")
        check(plans[2][1] == "click" and plans[3][1] == "press_key", "裸点击/按键不被抢走")
        check(all(item[1] != "click_screen" for item in plans[4:]),
              "疑问句/否定句/引用句/假设句都不触发 click_screen")
        check(plans[5][1] == "chat" and plans[6][1] != "click_screen",
              "明确否定点击只回话不动手")

        saved_user_text = getattr(jarvis._TL, "user_text", "")
        reset()
        jarvis._TL.user_text = "打开微信"
        jarvis.execute_plan({"action": "click_screen", "args": {"target": "微信按钮"}})
        check(not seen["clicks"] and any("明确说要点" in line for line in seen["spoken"]),
              "执行前复核原话: 无点击意图的计划不点并反问")
        reset()
        jarvis._TL.user_text = "我说点击发送会怎样"
        jarvis.execute_plan({"action": "click_screen", "args": {"target": "发送按钮"}})
        check(not seen["clicks"] and any("明确说要点" in line for line in seen["spoken"]),
              "执行前复核原话: 引用/假设句不点并反问")
        reset()
        jarvis._TL.user_text = "点击进入微信"
        jarvis.execute_plan({"action": "click_screen", "args": {"target": "进入微信按钮"}})
        check(len(seen["clicks"]) == 1, "执行前复核原话: 明确点击意图放行")
        reset()
        jarvis._TL.user_text = "点击进入微信"
        jarvis.execute_plan({"action": "click_screen", "args": {"target": "删除好友按钮"}})
        check(not seen["clicks"] and any("明确说要点" in line for line in seen["spoken"]),
              "执行前复核原话: 计划目标与原话对不上时反问")
        jarvis._TL.user_text = saved_user_text

        reset()
        jarvis._sensitive_window_hit = lambda: True
        out = jarvis._click_screen("进入微信按钮", allow_confirm=False)
        check(not seen["clicks"] and "敏感" in out, "敏感画面且不允许确认: 跳过不点")
        out = jarvis._click_screen("进入微信按钮")
        check(not seen["clicks"] and "确定要看" in out
              and jarvis._PEEK_CONFIRM.get("action") == "click_screen",
              "敏感画面复用 peek 确认政策, 登记 click_screen 待确认")

        reset()
        jarvis._PEEK_CONFIRM.update(pending=True, question="进入微信按钮", ts=time.time(),
                                    window=("hwnd-1", "微信"), source="local",
                                    action="click_screen", target="进入微信按钮",
                                    click_confirmed=False)
        jarvis._sensitive_window_hit = lambda: False  # 确认后复检: 窗口已不敏感才发图
        consumed = jarvis._peek_confirm_answer("确定", "local")
        check(consumed and len(seen["clicks"]) == 1, "敏感确认后恢复点击, 仍只点一次")
    finally:
        pyautogui.screenshot = REAL["screenshot"]
        pyautogui.click = REAL["click"]
        llm_brain.query_vision_locate = REAL["locate"]
        llm_brain.query_vision = REAL["vision"]
        jarvis._sensitive_window_hit = REAL["sensitive"]
        jarvis._foreground_window_identity = REAL["identity"]
        jarvis._guard_confirm = REAL["guard"]
        jarvis._CLICK_VERIFY_DELAY = REAL["peak_delay"]
        jarvis.speak = REAL["speak"]
        jarvis.ui_bridge = REAL["ui"]
        jarvis._PEEK_CONFIRM.update(pending=False, question="", ts=0.0, window=(),
                                    source="local", action="peek_screen", target="",
                                    click_confirmed=False)

    print()
    if fails:
        print(f"失败 {len(fails)} 项")
        for item in fails:
            print(" -", item)
        return 1
    print("PASS: 看屏点击 click_screen 全部打桩回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
