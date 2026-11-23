"""一次性真机验证: 用用户真实配置调一次视觉模型 (消耗极少量 token)。

目的: 确认 deepseek-flash 在这把 key 下能读图, 而不是只在文档里"支持"。
"""
import base64
import io
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from PIL import Image, ImageDraw   # noqa: E402

import jarvis                       # noqa: E402
from llm_brain import query_vision  # noqa: E402

img = Image.new("RGB", (420, 200), (250, 250, 250))
d = ImageDraw.Draw(img)
d.rectangle([10, 10, 410, 190], outline=(20, 20, 20), width=3)
d.text((30, 70), "JARVIS-OK 9527", fill=(10, 10, 10))
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=85)
b64 = base64.b64encode(buf.getvalue()).decode("ascii")

print("配置: provider=%s model=%s vision_model=[%s]" % (
    jarvis.CONFIG.get("ai_provider"), jarvis.CONFIG.get("ai_model"),
    jarvis.CONFIG.get("vision_model")))
try:
    ans = query_vision("这张图里写了什么？只回答上面的文字。", b64, jarvis.CONFIG)
    print("模型回答:", ans)
    ok = "JARVIS-OK" in ans.upper() or "9527" in ans
    print("判定:", "通过（真看懂图了）" if ok else "可疑：没读出文字")
    sys.exit(0 if ok else 1)
except Exception as exc:
    print("调用失败:", exc)
    sys.exit(2)
