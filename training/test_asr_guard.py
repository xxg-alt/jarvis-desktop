# -*- coding: utf-8 -*-
"""语音识别防幻觉/防误执行回归:
    1. "好、好、好…" 这类重复解码幻觉必须被丢弃;
    2. 底噪不送模型, 归一化增益封顶 (旧版能把底噪放大几十倍);
    3. 低置信度识别只提示"没听清", 不许拿鬼话去动电脑。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import whisper_engine
import jarvis

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# 1. 重复幻觉 (13:00 那条真发生过, 并真去读了你好.txt)
check(whisper_engine._clean("好的、好、好、好、好、好、好、好、好、") == "",
      "顿号分隔的重复幻觉应被丢弃")
check(whisper_engine._clean("好好好好好好好好") == "", "单字刷屏应被丢弃")
check(whisper_engine._clean("你好你好你好你好") == "", "短语四连重复应被丢弃")
# 正常说话不能被误杀
check(whisper_engine._clean("好的好的") == "好的好的", "两连重复是正常口语, 不该误杀")
check(whisper_engine._clean("打开微信") == "打开微信", "正常指令被改坏了")
check(whisper_engine._clean("帮我写个周报") == "帮我写个周报", "正常指令被改坏了")
# 原有黑名单仍然生效
check(whisper_engine._clean("感谢观看") == "", "黑名单失效")

# 2. 底噪不进模型
quiet = np.full(16000, 0.0005, dtype=np.float32)
check(whisper_engine._normalize(quiet) is None, "底噪应返回 None, 不该被放大后送模型")

# 3. 归一化增益封顶 (峰值 0.01 若按旧逻辑会放大 90 倍)
loud = np.zeros(16000, dtype=np.float32)
loud[100] = 0.01
out = whisper_engine._normalize(loud)
peak_out = float(np.max(np.abs(out))) if out is not None else -1
check(out is not None and peak_out <= 30.0 * 0.01 + 1e-6, f"增益没有封顶: peak={peak_out}")
check(out is not None and peak_out > 0.15, f"小声说话没被放大: peak={peak_out}")

# 4. 短音频 / 底噪不触发模型加载
d = whisper_engine.transcribe_detail(b"\x00\x00" * 100, size="small")
check(d.get("text") == "" and d.get("reason") == "太短", f"短音频不该识别: {d}")
d2 = whisper_engine.transcribe_detail(np.full(16000, 16, dtype=np.int16).tobytes(), size="small")
check(d2.get("text") == "" and d2.get("reason") == "底噪", f"底噪不该识别: {d2}")

# 5. 置信度闸门
check(jarvis._asr_uncertain({"logprob": -1.2}) is True, "低置信度应判为不确定")
check(jarvis._asr_uncertain({"logprob": -0.4}) is False, "正常置信度被误判")
check(jarvis._asr_uncertain({}) is False, "meta 缺失应保持旧行为(可执行)")
check(jarvis._asr_uncertain(None) is False, "meta=None 应保持旧行为(可执行)")

# 6. 唤醒词兜底: 只要喊了贾维斯, 低置信度也放行 (走 _wake_hit 判定, 不在此拦截)
check(jarvis._asr_uncertain({"logprob": -1.5}) is True, "阈值判定本身要成立")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS: 重复幻觉拦截 / 底噪不进模型 / 增益封顶 / 低置信度不执行")
