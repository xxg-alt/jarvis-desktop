# -*- coding: utf-8 -*-
"""自由对话通道分流测试: 指令必须留在命令通道, 聊天必须进对话通道。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import jarvis
import llm_brain

MUST_STAY_COMMAND = [
    "把下载里的PDF列个清单存成文档",
    "明天合肥天气怎么样",
    "帮我查一下现在的金价",
    "关机",
    "写一份学习总结存成文档",
    "搜索一下Python最新版本",
    "把微信关掉",
    "检查一下电脑内存占用",
    "微信打不开了怎么办",
]
MUST_GO_CHAT = [
    "唉，今天上课好累",
    "你觉得我转AI划不划算",
    "给我讲讲黑洞是怎么形成的",
    "水的沸点是多少度",
    "陪我聊聊天吧",
    "我最近压力大，睡不着",
    "讲个笑话听听",
    "你怎么看大学生要不要考研",
]

fails = []
for s in MUST_STAY_COMMAND:
    if jarvis._looks_conversational(s):
        fails.append(f"指令被误放进聊天: {s}")
for s in MUST_GO_CHAT:
    if not jarvis._looks_conversational(s):
        fails.append(f"聊天没被识别: {s}")

# free_chat 桩测: 假模型回一句话, 检查清洗/历史/上下文拼接
seen = {}
def fake_chat(config, messages, max_tokens=None, json_mode=False):
    seen["messages"] = messages
    return "  当然记得，\n你上次说的是先把 Unity 的 30 天桥接练完再决定。  "
llm_brain._provider_chat = fake_chat
llm_brain._CHAT_SESSION.clear()
reply = llm_brain.free_chat("就按上次那个方案吧", {}, memory_context="【主人档案】ljj")
if "Unity" not in reply or "\n" in reply:
    fails.append(f"free_chat 清洗不对: {reply!r}")
if len(llm_brain._CHAT_SESSION) != 2:
    fails.append("free_chat 历史没记上")
if "主人档案" not in seen["messages"][-1]["content"]:
    fails.append("free_chat 没把记忆拼进本轮消息")
if seen["messages"][0]["role"] != "system" or "JSON" not in seen["messages"][0]["content"]:
    fails.append("free_chat 系统提示不对")

# free_chat 失败要返回空串 (调用方回退规划通道)
def boom(config, messages, max_tokens=None, json_mode=False):
    raise llm_brain._LlmError("连接失败")
llm_brain._provider_chat = boom
if llm_brain.free_chat("你好，聊聊吧", {}) != "":
    fails.append("free_chat 失败时应返回空串")

# reset_session 连聊天历史一起清
llm_brain.reset_session()
if llm_brain._CHAT_SESSION:
    fails.append("reset_session 没清聊天历史")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print(f"PASS: 分流 {len(MUST_STAY_COMMAND) + len(MUST_GO_CHAT)} 句全对, free_chat 桩测全过")
