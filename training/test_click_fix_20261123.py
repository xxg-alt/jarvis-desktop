"""回归: 2026-11-23 日志里两个真实故障。

故障一 (微信没点动):
  "帮我点击进入微信的按钮" → 视觉定位返回空 (finish_reason=length, token 预算 120 烧在思考里)
  → 坐标拿不到 → "没找到那个按钮"。
  后续 "…按钮在哪里然后帮我点击" / "点进录微信的按钮啊" / "在库狗音乐里点几波放音乐" 全被
  点击闸门判成"没有明确命令", 一句都没点到屏幕上。

故障二 (酷狗):
  "播我怀念的" → 开的是网易云网页, 不是本机酷狗; 用户点名了酷狗却被换成网页。

本测试锁住修完的这几条判定。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis          # noqa: E402
import llm_brain       # noqa: E402

FAILS = []


def check(cond, name, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ── ① 点击意图解析: 日志里被拒的原句 ──
CASES = [
    ("帮我点击进入微信的按钮", "command", "进入微信的按钮"),
    ("截圖看一下那个进入微信的按钮在哪里然后帮我点击", "command", "进入微信的按钮"),
    ("点进录微信的按钮啊", "command", "录微信的按钮啊"),
    ("我是Lonnie,在我的库狗音乐里面点几波放音乐的按钮", "command", "放音乐的按钮"),
    ("不要点击删除", "negated", ""),                        # 否定照旧拦住
    ("点击发送会怎样", "unclear", ""),                      # 假设/引用照旧拦住
    ("进入微信的爱鸟", "unclear", ""),                      # 没有点击动词: 宁可反问
]
for text, want_state, want_target in CASES:
    target, state = jarvis._click_intent(text)
    check(state == want_state, f"闸门 state: {text[:16]}… → {state}", f"want={want_state}")
    if want_target:
        check(jarvis._click_target_close(target, want_target),
              f"闸门 target: {text[:16]}… → {target}", f"want≈{want_target}")

# 富余纠正: 不再靠逐词字表, 走拼音层 —— "库狗"和"酷狗"拼音同音
check(jarvis._pinyin("库狗音乐") == jarvis._pinyin("酷狗音乐"), "拼音层: 库狗 ≡ 酷狗")
check(jarvis._click_target_close("库狗音乐", "酷狗音乐"), "相似度: ASR 同音字不误伤")
check(jarvis._asr_fix("那个按纽") == "那个按钮", "字表只留与具体应用无关的纯错字")

# 拼音兜底动词: 精确正则抓不到的错字动词
t5, s5 = jarvis._click_intent("在我的酷狗音乐里面点几波放音乐的按钮")
check(s5 == "command" and jarvis._click_target_close(t5, "放音乐的按钮"),
      f"拼音兜底动词: 点几波 → command", f"got=({t5!r},{s5})")

# ── ② target 相似度: 不再因为一个错字整句拒 ──
check(jarvis._click_target_close("进入微信的按钮", "进入微信按钮"), "相似度: 同义换写法")
check(not jarvis._click_target_close("进入微信按钮", "删除好友"), "相似度: 完全不同的按钮不认")

# ── ③ 视觉定位空返回要重试, 且预算不再只给 120 ──
calls = []


def fake_provider_chat(config, messages, max_tokens=None, json_mode=False):
    calls.append(max_tokens)
    if len(calls) == 1:
        return ""          # 复刻线上: finish_reason=length 但正文为空
    return '{"x_min":100,"y_min":200,"x_max":300,"y_max":260}'


real = llm_brain._provider_chat
llm_brain._provider_chat = fake_provider_chat
try:
    out = llm_brain.query_vision_locate("进入微信的按钮", "AAAA", {"ai_provider": "DeepSeek"})
finally:
    llm_brain._provider_chat = real

check(len(calls) == 2, "空定位会重发一次", f"calls={calls}")
check("x_min" in out, "重发后拿到坐标", f"out={out!r}")
check(calls and calls[0] >= 600, "定位预算 ≥600 tokens (原 120 烧在思考里)", f"calls={calls}")
check(len(calls) >= 2 and calls[1] > calls[0], "重发时预算再放大", f"calls={calls}")

# ── ④ 音乐路由: 通用守门, 不认特定厂商 ──
prompt = llm_brain.SYSTEM_PROMPT
check("绝不用 open_url 打开音乐网站" in prompt, "提示词禁止用网页顶替音乐客户端(通用表述)")
check("music_app" in prompt, "提示词默认客户端由 config 决定, 不写死")

saved_user = getattr(jarvis._TL, "user_text", "")
saved_cfg = dict(jarvis.CONFIG)
try:
    jarvis._TL.user_text = "播我怀念的这首歌"
    jarvis.CONFIG["music_app"] = "网易云音乐"
    p = jarvis._music_web_guard({"action": "open_url",
                                 "args": {"url": "https://music.163.com/#/search/m/?s=x"},
                                 "say": "打开网页"})
    check(p["action"] == "open_app" and p["args"]["name"] == "网易云音乐",
          "守门: 音乐网页拦回 config 指定的客户端", f"got={p}")
    jarvis._TL.user_text = "用浏览器打开网易云音乐网页"
    p2 = jarvis._music_web_guard({"action": "open_url",
                                  "args": {"url": "https://music.163.com"},
                                  "say": "打开网页"})
    check(p2["action"] == "open_url", "守门: 用户明确说网页/浏览器时放行")
    p3 = jarvis._music_web_guard({"action": "open_url",
                                  "args": {"url": "https://github.com/xxg-alt"},
                                  "say": "打开"})
    check(p3["action"] == "open_url", "守门: 非音乐网站不拦")
finally:
    jarvis._TL.user_text = saved_user
    jarvis.CONFIG.clear(); jarvis.CONFIG.update(saved_cfg)

print()
if FAILS:
    print("失败项:", FAILS)
    sys.exit(1)
print("结果: 全部通过")
