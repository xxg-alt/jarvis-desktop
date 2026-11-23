"""回归: 看屏 peek_screen —— 图只在内存里, 磁盘上不留任何文件。

用户明确要求: "临时性、用完即删"。做法不是写盘再删, 而是根本不写:
  pyautogui.screenshot() → PIL 对象 → BytesIO 压 JPEG → base64 → 请求体
本测试守住三点:
  ① 分流: 这类话必须命中 peek_screen, 不能落到 screenshot(会存盘)
  ② 零落盘: 截一张 3000x2000 的假屏, 截图目录与工作区的文件数一个都不能变
  ③ 请求体形状: OpenAI 兼容的 content 块数组 + data URL + detail=high
"""
import base64
import io
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from PIL import Image   # noqa: E402
import pyautogui        # noqa: E402

import jarvis           # noqa: E402
import llm_brain        # noqa: E402


def count_files(root: Path):
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def main():
    fails = []

    # ── 一、分流 ──
    routing = [
        ("看看屏幕上这个是什么", "peek_screen"),
        ("屏幕上的报错什么意思", "peek_screen"),
        ("识别一下屏幕上写了什么", "peek_screen"),
        ("这个页面是什么", "peek_screen"),
        ("不要看屏幕了", "chat"),
        ("截图", "screenshot"),           # 存档型截图没被抢走
        ("你截图的文件放到哪个文件夹里？", "chat"),
    ]
    for text, want in routing:
        got = (jarvis.parse_command(text) or {}).get("action")
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  分流 {text!r:22} → {got} (期望 {want})")
        if not ok:
            fails.append(("分流", text, got, want))

    # ── 二、内存成像 + 零落盘 ──
    shot_dir = jarvis.screenshot_dir()
    ws_dir = jarvis.agent_workspace()
    before_shot, before_ws = count_files(shot_dir), count_files(ws_dir)

    real_screenshot = pyautogui.screenshot
    fake_screen = Image.new("RGB", (3000, 2000), (18, 24, 36))
    pyautogui.screenshot = lambda *a, **k: fake_screen.copy()
    try:
        b64, size = jarvis._screen_jpeg_b64()
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        long_edge = max(img.size)
        ok = (long_edge <= jarvis._PEEK_LONG_EDGE and img.format == "JPEG"
              and a_jpeg_magic(raw))
        print(f"{'PASS' if ok else 'FAIL'}  成像 {size} 长边={long_edge} 格式={img.format}")
        if not ok:
            fails.append(("成像", size, long_edge, img.format))
    finally:
        pyautogui.screenshot = real_screenshot

    after_shot, after_ws = count_files(shot_dir), count_files(ws_dir)
    ok = after_shot == before_shot and after_ws == before_ws
    print(f"{'PASS' if ok else 'FAIL'}  零落盘 截图目录 {before_shot}→{after_shot} / 工作区 {before_ws}→{after_ws}")
    if not ok:
        fails.append(("零落盘", before_shot, after_shot, before_ws, after_ws))

    # ── 三、请求体形状 (桩掉 _provider_chat, 不花钱不联网) ──
    captured = {}
    real_chat = llm_brain._provider_chat

    def fake_chat(config, messages, max_tokens=None, json_mode=False):
        captured["messages"] = messages
        return "我看到一个深色的窗口，标题栏写着设置。"

    llm_brain._provider_chat = fake_chat
    try:
        ans = llm_brain.query_vision("这个页面是什么", "QUJD", {"ai_provider": "DeepSeek"})
    finally:
        llm_brain._provider_chat = real_chat

    msg = (captured.get("messages") or [{}, {}])[-1]
    parts = msg.get("content")
    ok = (isinstance(parts, list) and len(parts) == 2
          and parts[0].get("type") == "text"
          and parts[1].get("type") == "image_url"
          and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,QUJD")
          and parts[1]["image_url"]["detail"] == "high"
          and "深色" in ans)
    print(f"{'PASS' if ok else 'FAIL'}  请求体 content=块数组, data URL, detail=high → {ans!r}")
    if not ok:
        fails.append(("请求体", parts))

    # ── 四、动作执行走通(外观层桩掉 _peek_screen, 验证接线) ──
    real_peek = jarvis._peek_screen
    jarvis._peek_screen = lambda q="", **kwargs: f"桩回答: {q}"
    try:
        say = jarvis._perform_plan({"action": "peek_screen", "args": {"question": "这是什么"}})
    finally:
        jarvis._peek_screen = real_peek
    ok = "桩回答" in str(say) and "这是什么" in str(say)
    print(f"{'PASS' if ok else 'FAIL'}  动作接线 → {say!r}")
    if not ok:
        fails.append(("接线", say))

    print()
    if fails:
        print(f"失败 {len(fails)} 条")
        for f in fails:
            print("  ", f)
        return 1
    print(f"全部通过 ({len(routing)} 条分流 + 成像/零落盘/请求体/接线)")
    return 0


def a_jpeg_magic(raw: bytes) -> bool:
    return raw[:3] == b"\xff\xd8\xff"


if __name__ == "__main__":
    sys.exit(main())
