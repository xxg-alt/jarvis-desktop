"""一次性真机验证: 完整走一遍 peek_screen (伪造屏幕, 不截真实桌面)。

验证 截图→内存压图→base64→视觉模型→回答 整条链路, 并确认没有落盘。
"""
import io
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from PIL import Image, ImageDraw, ImageFont   # noqa: E402
import pyautogui                    # noqa: E402

import jarvis                       # noqa: E402


def count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.exists() else 0


# 伪造一张"屏幕": 按真实分辨率 1707x1067 + 真实字号 (~20px)，模拟报错弹窗
img = Image.new("RGB", (1707, 1067), (30, 40, 55))
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
except Exception:
    font = ImageFont.load_default()
d.rectangle([380, 320, 1330, 760], fill=(48, 56, 72), outline=(120, 200, 230), width=3)
d.text((430, 380), "ERROR 0x80070005", fill=(255, 120, 120), font=font)
d.text((430, 450), "Permission denied", fill=(230, 230, 230), font=font)
d.text((430, 520), "C:\\Program Files\\Jarvis\\jarvis.exe", fill=(200, 200, 200), font=font)

shot_dir, ws_dir = jarvis.screenshot_dir(), jarvis.agent_workspace()
before = (count_files(shot_dir), count_files(ws_dir))

real = pyautogui.screenshot
pyautogui.screenshot = lambda *a, **k: img.copy()
try:
    answer = jarvis._peek_screen("屏幕上这个弹窗在报什么错？把错误码和路径读出来。")
finally:
    pyautogui.screenshot = real

after = (count_files(shot_dir), count_files(ws_dir))
print("回答:", answer)
print(f"落盘检查: 截图目录 {before[0]}→{after[0]}, 工作区 {before[1]}→{after[1]}")
ok = ("0x80070005" in answer.upper().replace(" ", "")) or ("permission" in answer.lower())
no_disk = before == after
print("判定:", "通过" if (ok and no_disk) else "不通过")
sys.exit(0 if (ok and no_disk) else 1)
