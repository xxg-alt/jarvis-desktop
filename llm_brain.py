# -*- coding: utf-8 -*-
"""
贾维斯通用 AI 大模型适配大脑 (Multi-LLM Brain)
支持:
1. OpenAI 格式兼容大模型 (DeepSeek / 通义千问 / 智谱清言 / Kimi / MiniMax / OpenAI)
2. Google Gemini 官方 API
支持直接在全屏 UI 或 config.json 中配置 API Key
"""
import json
import os
import re
import requests
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 预设主流 AI 大模型服务商 (国内直连/官方直连)
LLM_PROVIDERS = {
    "DeepSeek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "type": "openai_compatible"
    },
    "通义千问 (Qwen)": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
        "type": "openai_compatible"
    },
    "智谱清言 (GLM)": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-4-flash",
        "type": "openai_compatible"
    },
    "Moonshot (Kimi)": {
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "moonshot-v1-8k",
        "type": "openai_compatible"
    },
    "Google Gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "default_model": "gemini-1.5-flash",
        "type": "gemini"
    },
    "OpenAI (ChatGPT)": {
        "url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "type": "openai_compatible"
    },
    "自定义 API (OpenAI兼容)": {
        "url": "",
        "default_model": "",
        "type": "openai_compatible"
    }
}

SYSTEM_PROMPT = """你是一个全能的 Windows 桌面智能语音助手贾维斯 (JARVIS)。
当用户下达控制电脑的指令时，请将其转换成控制动作并回复；
当用户提出日常问题、聊天、写代码、分析问题或请教知识时，请用贾维斯忠诚、专业且富有人性化的口吻直接回答！

只能输出纯 JSON，格式如下：
{
  "action": "动作名(必须是下述之一: chat, open_app, open_url, web_search, screenshot, lock, volume)",
  "args": {动作参数，chat 动作留空即可},
  "say": "对用户的中文回答"
}

说明：
- 如果是普通聊天、提问或解答：action 填 "chat"，say 填你的详细专业回答。
- 如果是打开软件：action 填 "open_app"，args 填 {"exe": "软件exe名", "name": "软件名"}
- 如果是搜索：action 填 "web_search"，args 填 {"query": "搜索关键词"}
- 如果是静音/调音量：action 填 "volume"，args 填 {"mode": "up/down/mute"}
- 如果是截图：action 填 "screenshot"
- 如果是锁屏：action 填 "lock"
注意：不要输出任何 markdown 格式代码块，只返回纯 JSON 字符串！
如果提供了【记忆上下文】，用它理解指代、延续话题和用户纠正；历史只作参考，
当前用户输入才是本次唯一任务，不要重复执行历史动作。
"""

_SHELL_META_RE = re.compile(r"[&|;<>`\r\n\x00]")

# API Key 只允许可见 ASCII 且不含空格: 粘错内容 (大段文字/换行) 会直接请求头非法
_API_KEY_RE = re.compile(r"^[\x21-\x7E]{8,200}$")


def clean_api_key(raw) -> str:
    """校验并返回可用的 API Key; 格式不对返回空串 (而不是拿一段文字去发请求)"""
    key = str(raw or "").strip()
    return key if _API_KEY_RE.match(key) else ""


def _safe_text(value, limit=200):
    """参数只接受干净的短字符串: 去掉夹带的 shell 元字符, 超长直接判非法。"""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    if not v or len(v) > limit or _SHELL_META_RE.search(v):
        return ""
    return v


def sanitize_plan(plan: dict) -> dict:
    """统一收敛大模型输出: 只保留白名单动作, 并逐个校验参数取值。

    这是唯一的出口关卡 —— 模型返回什么都要过这一遍, 之后新增 action 只需在这里补规则。
    原则: 参数只当"数据"用, 绝不拼进命令行; 拿不准就降级成 chat。
    """
    if not isinstance(plan, dict):
        return {"action": "chat", "say": "这个动作我暂时做不了"}

    action = plan.get("action")
    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
    say = plan.get("say") if isinstance(plan.get("say"), str) else ""
    say = say[:2000]

    if action == "open_app":
        exe = _safe_text(args.get("exe"), 260)
        if not exe:
            return {"action": "chat", "say": "这个软件名我没法直接启动, Sir"}
        name = _safe_text(args.get("name"), 60) or exe
        return {"action": "open_app", "args": {"exe": exe, "name": name}, "say": say or f"已打开{name}"}

    if action == "open_url":
        url = _safe_text(args.get("url"), 2048)
        if not re.match(r"^https?://", url, re.I):
            return {"action": "chat", "say": "我只打开 http 或 https 的网址, Sir"}
        return {"action": "open_url", "args": {"url": url}, "say": say or "已打开网页"}

    if action == "web_search":
        query = _safe_text(args.get("query"), 200)
        if not query:
            return {"action": "chat", "say": "要搜索什么呢, Sir?"}
        return {"action": "web_search", "args": {"query": query}, "say": say or f"正在搜索{query}"}

    if action == "volume":
        mode = args.get("mode")
        if mode not in ("up", "down", "mute"):
            return {"action": "chat", "say": "音量参数我理解不了, Sir"}
        out_args = {"mode": mode}
        if mode in ("up", "down"):
            try:
                steps = int(args.get("steps", 5))
            except (TypeError, ValueError):
                steps = 5
            out_args["steps"] = max(1, min(50, steps))
        return {"action": "volume", "args": out_args, "say": say or "已调整音量"}

    if action in ("screenshot", "lock"):
        return {"action": action, "args": {}, "say": say}

    # chat 以及任何不认识的 action 一律降级为纯聊天
    return {"action": "chat", "say": say or "这个动作我暂时做不了"}


def query_llm_plan(command: str, config: dict, memory_context: str = "") -> dict:
    """根据配置的提供商与 API Key 调用大模型。"""
    provider_name = config.get("ai_provider", "DeepSeek")
    raw_key = (config.get("api_key") or "").strip() or os.environ.get("AI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    api_key = clean_api_key(raw_key)

    if not api_key:
        if raw_key:
            # 常见于把聊天内容/带空行的文字粘进了密钥框
            print(f"[AI 密钥格式不对] 密钥应是一串不含空格的字符, 当前 {len(raw_key)} 字符; 请在界面里重新粘贴")
        return None

    provider_info = LLM_PROVIDERS.get(provider_name, LLM_PROVIDERS["DeepSeek"])
    model = config.get("ai_model", "").strip() or provider_info["default_model"]
    api_url = config.get("ai_url", "").strip() or provider_info["url"]

    try:
        # 1. OpenAI 兼容协议 (DeepSeek / 通义千问 / 智谱 / Kimi / OpenAI 等)
        if provider_info["type"] == "openai_compatible":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            system_content = SYSTEM_PROMPT
            if memory_context:
                system_content += "\n\n" + str(memory_context)
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": command}
                ],
                "temperature": 0.7,
                "response_format": {"type": "json_object"} if "deepseek" in model or "gpt" in model else None
            }
            if payload["response_format"] is None:
                del payload["response_format"]

            r = requests.post(api_url, headers=headers, json=payload, timeout=25)
            if r.status_code != 200:
                print(f"[{provider_name} 错误] HTTP {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            raw_text = data["choices"][0]["message"]["content"].strip()

        # 2. Google Gemini 协议
        elif provider_info["type"] == "gemini":
            target_url = api_url.format(model=model)
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json"
            }
            prompt_text = SYSTEM_PROMPT
            if memory_context:
                prompt_text += "\n\n" + str(memory_context)
            prompt_text += "\n用户输入: " + command
            payload = {
                "contents": [{
                    "parts": [{"text": prompt_text}]
                }]
            }
            r = requests.post(target_url, headers=headers, json=payload, timeout=25)
            if r.status_code != 200:
                print(f"[Gemini 错误] HTTP {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # 解析模型返回的 JSON
        clean_text = re.sub(r"^```(json)?|```$", "", raw_text, flags=re.M).strip()
        plan = json.loads(clean_text)

        # 出口统一校验: 白名单动作 + 参数合法性
        return sanitize_plan(plan)

    except Exception as e:
        msg = str(e)
        # 不要把异常原文整个打出来: requests 的 InvalidHeader 会把 Bearer 后面那串带进日志
        if "header value" in msg or type(e).__name__ == "InvalidHeader":
            print("[大模型调用失败] 请求头不合法, 密钥里可能混入了空格/中文, 请重新粘贴")
        else:
            print(f"[大模型调用失败] {type(e).__name__}: {msg[:160]}")
        return None
