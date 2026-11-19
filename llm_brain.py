# -*- coding: utf-8 -*-
"""
贾维斯通用 AI 大模型适配大脑 (Multi-LLM Brain)
支持:
1. OpenAI 格式兼容大模型 (DeepSeek / 通义千问 / 智谱清言 / Kimi / OpenAI / 自定义)
2. Google Gemini 官方 API
3. Anthropic Claude 官方 API
支持直接在全屏 UI 或 config.json 中配置 API Key、模型名和接口地址
"""
import json
import os
import re
from urllib.parse import urlsplit

import requests
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 预设主流 AI 大模型服务商 (国内直连/官方直连)
# 模型清单对齐 2026-11 各厂商 API 现役版本；deepseek-chat/reasoner 是官方别名，
# 永远指向最新一代模型，保留在最前作为稳妥默认值。
LLM_PROVIDERS = {
    "DeepSeek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner"],
        "type": "openai_compatible"
    },
    "通义千问 (Qwen)": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen3.7-plus",
        "models": ["qwen3.7-plus", "qwen3.8-flash", "qwen3.8-max-0902",
                   "qwen3.7-flash", "qwen4-max-preview", "qwen-plus"],
        "type": "openai_compatible"
    },
    "智谱清言 (GLM)": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-5.3-flash",
        "models": ["glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-4.7",
                   "glm-4.5-flash"],
        "type": "openai_compatible"
    },
    "Moonshot (Kimi)": {
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "kimi-k3",
        "models": ["kimi-k3", "kimi-k2.6", "kimi-k3.1-pro",
                   "kimi-k3.1-pro-highspeed", "kimi-k2.7-code"],
        "type": "openai_compatible"
    },
    "Google Gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "default_model": "gemini-3.1-pro-preview",
        "models": ["gemini-3.1-pro-preview", "gemini-3-flash-preview",
                   "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
        "type": "gemini"
    },
    "OpenAI (ChatGPT)": {
        "url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-5.2",
        "models": ["gpt-5.2", "gpt-5.2-chat", "gpt-5.3-codex", "gpt-5.1",
                   "gpt-4o-mini"],
        "type": "openai_compatible"
    },
    "Anthropic Claude": {
        "url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-sonnet-5-5",
        "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5",
                   "claude-fable-5-2", "claude-sonnet-4-6"],
        "type": "anthropic"
    },
    "自定义 API (OpenAI兼容)": {
        "url": "",
        "default_model": "",
        "models": [],
        "type": "openai_compatible"
    }
}

# 兼容旧界面/手写配置里的常见简称，避免因为名称不精确就报“未知服务商”
PROVIDER_ALIASES = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI (ChatGPT)",
    "openai (chatgpt)": "OpenAI (ChatGPT)",
    "chatgpt": "OpenAI (ChatGPT)",
    "claude": "Anthropic Claude",
    "anthropic": "Anthropic Claude",
    "anthropic claude": "Anthropic Claude",
    "通义千问": "通义千问 (Qwen)",
    "qwen": "通义千问 (Qwen)",
    "kimi": "Moonshot (Kimi)",
    "moonshot": "Moonshot (Kimi)",
    "moonshot (kimi)": "Moonshot (Kimi)",
    "智谱": "智谱清言 (GLM)",
    "glm": "智谱清言 (GLM)",
    "gemini": "Google Gemini",
    "google gemini": "Google Gemini",
    "自定义": "自定义 API (OpenAI兼容)",
    "自定义 api": "自定义 API (OpenAI兼容)",
    "自定义 api (openai兼容)": "自定义 API (OpenAI兼容)",
}


def resolve_provider_name(raw_name) -> str:
    """把服务商名称收敛到 LLM_PROVIDERS 的精确键；未知返回空串。"""
    name = str(raw_name or "").strip()
    if name in LLM_PROVIDERS:
        return name
    return PROVIDER_ALIASES.get(name.lower(), "")


def normalize_openai_endpoint(raw_url) -> str:
    """允许用户只填域名或 /v1 基址；自动补成 chat/completions 完整地址。"""
    url = str(raw_url or "").strip().rstrip("/")
    if not url or not re.match(r"^https?://", url, re.I):
        return ""
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1") or url.endswith("/compatible-mode/v1"):
        return url + "/chat/completions"
    parts = urlsplit(url)
    if parts.path in ("", "/"):
        return url + "/v1/chat/completions"
    return url

SYSTEM_PROMPT = """你是一个全能的 Windows 桌面智能语音助手贾维斯 (JARVIS)。
当用户下达控制电脑的指令时，请将其转换成控制动作并回复；
当用户提出日常问题、聊天、写代码、分析问题或请教知识时，请用贾维斯忠诚、专业且富有人性化的口吻直接回答！

你必须记住自己的真实能力边界：
- 你运行在用户的 Windows 桌面上，用户通过麦克风说话，本地语音识别会把话转成文字再交给你；因此不要声称“我只能接收文字”“我听不到声音”或“请把话打出来”。
- 如果用户问“你听得到我说话吗”，应回答“能听到，我是通过本地语音识别接收您的语音的”，然后继续正常交流。
- 回答默认简洁，控制在 1~3 句；除非用户明确要求详细展开，不要长篇大论。

只能输出纯 JSON，格式如下：
{
  "action": "动作名(必须是下述之一: chat, open_app, open_url, web_search, screenshot, lock, volume, file_manage, inspect_system, run_powershell)",
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

【文件管理】action 填 "file_manage"，args 从下面三种里选一种：
  {"action": "list", "folder": "下载"}                      列目录（folder 可写 桌面/下载/文档）
  {"action": "search", "pattern": "*.pdf"}                   按通配符找文件
  {"action": "move", "src": "下载\\报表.xlsx", "dst": "文档"}  归类移动到某个目录
  只能操作桌面、下载、文档这三个目录。用户要删除文件时，如实说做不到，不要假装完成。

【系统状态】action 填 "inspect_system"，args 从下面三种里选一种：
  {"mode": "top_memory"}              最吃内存的前 5 个进程
  {"mode": "top_cpu"}                 最吃 CPU 的前 5 个进程
  {"mode": "port", "target": 8080}    谁占用了某个端口

【执行命令】action 填 "run_powershell"，args 填 {"command": "Get-Process"}：
  一次只给一条最简单的命令。不要用管道 |、重定向 >、分号 ;、反引号和换行，这些会被安全层拒绝。
  只读的查询命令（Get-*、tasklist、ipconfig 等）会直接执行；带副作用的命令会先让用户确认。
  关机、重启、删除文件、改注册表、改系统服务这类命令一律不要生成。

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


def key_hint(raw) -> str:
    """给界面看的密钥指纹: 只露头 6 位和尾 4 位。

    目的是让人一眼确认"存的是哪一把", 同时不把明文再送回页面 DOM。
    """
    key = str(raw or "").strip()
    if not key:
        return "未配置"
    if len(key) <= 12:
        return "已配置"
    return f"{key[:6]}…{key[-4:]}"


def probe_api_key(config: dict, api_key: str = "") -> tuple:
    """用这把 Key 对目标服务商发一次最小真实请求, 判断它是否真的属于该服务商。

    返回 (是否通过, 说明文字)。401/403 几乎总是"Key 与服务商不匹配"或已失效 ——
    这正是同一把第三方兼容网关的密钥被填进 DeepSeek 时必然 401 的原因。
    设计成只发 1 个 token 的探针请求, 成本可以忽略。
    """
    cfg = config or {}
    provider_name = resolve_provider_name(cfg.get("ai_provider", "DeepSeek"))
    if not provider_name:
        return False, "服务商无法识别，请重新选择"
    info = LLM_PROVIDERS[provider_name]
    key = clean_api_key(api_key or cfg.get("api_key"))
    if not key:
        return False, "没有可用的 API Key"
    model = str(cfg.get("ai_model") or "").strip() or info["default_model"]
    if not model:
        return False, "没有填写模型名"
    url = str(cfg.get("ai_url") or "").strip() or info["url"]
    if not url:
        return False, "没有配置接口地址"
    kind = info["type"]
    try:
        if kind == "openai_compatible":
            url = normalize_openai_endpoint(url) or url
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}
        elif kind == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                       "Content-Type": "application/json"}
            payload = {"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}
        else:  # gemini
            url = url.replace("{model}", model)
            headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
            payload = {"contents": [{"parts": [{"text": "ping"}]}],
                       "generationConfig": {"maxOutputTokens": 1}}

        r = requests.post(url, headers=headers, json=payload, timeout=10)
        status = r.status_code
        if status in (401, 403):
            return False, f"这把密钥被 {provider_name} 拒绝（HTTP {status}）：它不属于该服务商，或已失效"
        if status == 404:
            return False, f"{provider_name} 找不到该接口或模型（HTTP 404）：请检查接口地址与模型名"
        if status == 429:
            return True, f"{provider_name} 鉴权已通过，但当前被限流（HTTP 429），稍后再用即可"
        if 200 <= status < 300:
            return True, f"{provider_name} 鉴权通过"
        if 400 <= status < 500:
            return False, f"{provider_name} 返回 HTTP {status}：密钥可能有效，但请求被拒绝，请检查模型名"
        return False, f"{provider_name} 服务端异常（HTTP {status}），暂时无法确认密钥"
    except requests.exceptions.Timeout:
        return False, "校验超时：网络太慢或接口无响应，这把 Key 是否有效暂未确认"
    except requests.exceptions.ConnectionError:
        return False, "连不上接口：请检查网络、代理或接口地址"
    except Exception as exc:
        return False, f"校验未完成：{type(exc).__name__}"


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

    # 统一动作安全分类（白名单机制）
    # 绿灯（安全/只读）：chat, open_app, open_url, web_search, volume, screenshot, lock
    # 红/黄灯（危险/变更）：shutdown, restart, close_app, run_cmd, file_delete 等
    DANGEROUS_ACTIONS = {"shutdown", "restart", "close_app", "kill_process", "run_cmd", "file_delete", "file_move"}
    is_danger = bool(plan.get("danger")) or (action in DANGEROUS_ACTIONS)
    guard_desc = plan.get("guard_desc") or ""

    if action == "open_app":
        exe = _safe_text(args.get("exe"), 260)
        if not exe:
            return {"action": "chat", "say": "这个软件名我没法直接启动, Sir"}
        name = _safe_text(args.get("name"), 60) or exe
        return {"action": "open_app", "args": {"exe": exe, "name": name}, "say": say or f"已打开{name}", "danger": False}

    if action == "close_app":
        exe = _safe_text(args.get("exe"), 260)
        name = _safe_text(args.get("name"), 60) or exe
        if not exe:
            return {"action": "chat", "say": "请告诉我具体要关闭哪个程序, Sir"}
        return {
            "action": "close_app",
            "args": {"exe": exe, "name": name},
            "say": say or f"已关闭{name}",
            "danger": True,
            "guard_desc": f"关闭应用程序：{name} ({exe})"
        }

    if action in ("shutdown", "restart"):
        action_name = "关机" if action == "shutdown" else "重启系统"
        return {
            "action": action,
            "args": args,
            "say": say or f"准备{action_name}",
            "danger": True,
            "guard_desc": f"高危系统操作：{action_name}"
        }

    if action == "open_url":
        url = _safe_text(args.get("url"), 2048)
        if not re.match(r"^https?://", url, re.I):
            return {"action": "chat", "say": "我只打开 http 或 https 的网址, Sir"}
        return {"action": "open_url", "args": {"url": url}, "say": say or "已打开网页", "danger": False}

    if action == "web_search":
        query = _safe_text(args.get("query"), 200)
        if not query:
            return {"action": "chat", "say": "要搜索什么呢, Sir?"}
        return {"action": "web_search", "args": {"query": query}, "say": say or f"正在搜索{query}", "danger": False}

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
        return {"action": "volume", "args": out_args, "say": say or "已调整音量", "danger": False}

    # ---- 第二步新增：三个真实系统工具 ----
    if action == "file_manage":
        sub = _safe_text(args.get("action"), 20).lower()
        if sub not in ("list", "search", "move"):
            return {"action": "chat", "say": "文件这块我只会列目录、按名字找、归类移动。", "danger": False}

        out_args = {"action": sub}
        if sub == "list":
            folder = str(args.get("folder") or "").strip()
            if folder:
                out_args["folder"] = folder[:260]
        elif sub == "search":
            pattern = _safe_text(args.get("pattern"), 120)
            if not pattern:
                return {"action": "chat", "say": "要按什么找？比如 *.pdf 或者 *简历*。", "danger": False}
            out_args["pattern"] = pattern
            folder = str(args.get("folder") or "").strip()
            if folder:
                out_args["folder"] = folder[:260]
        else:  # move
            src = str(args.get("src") or "").strip()
            if not src:
                return {"action": "chat", "say": "要移动哪个文件？", "danger": False}
            out_args["src"] = src[:260]
            dst = str(args.get("dst") or "").strip()
            if dst:
                out_args["dst"] = dst[:260]

        # 路径只当数据交给 system_tools 做授权目录校验, 不拼任何命令行
        if sub == "move":
            return {
                "action": "file_manage",
                "args": out_args,
                "say": say or "这就帮你归档",
                "danger": True,
                "guard_desc": f"移动文件：{out_args['src']} → {out_args.get('dst') or '下载'}",
            }
        return {"action": "file_manage", "args": out_args, "say": say, "danger": False}

    if action == "inspect_system":
        kind = _safe_text(args.get("mode"), 20).lower()
        if kind not in ("top_memory", "top_cpu", "port"):
            return {"action": "chat", "say": "系统状态我只会查内存榜、CPU 榜和端口占用。", "danger": False}
        out_args = {"mode": kind}
        if kind == "port":
            try:
                port = int(args.get("target"))
            except (TypeError, ValueError):
                return {"action": "chat", "say": "端口号得是数字，比如 8080。", "danger": False}
            if not 0 < port < 65536:
                return {"action": "chat", "say": "端口号超出范围了。", "danger": False}
            out_args["target"] = port
        return {"action": "inspect_system", "args": out_args, "say": say, "danger": False}

    if action == "run_powershell":
        # _safe_text 会过滤 & | ; < > 反引号与换行, 正是文档要求的命令元字符黑名单
        command = _safe_text(args.get("command"), 1000)
        if not command:
            return {"action": "chat", "say": "要执行什么命令？", "danger": False}
        try:
            from system_tools import is_readonly_command
            readonly = is_readonly_command(command)
        except Exception:
            readonly = False
        return {
            "action": "run_powershell",
            "args": {"command": command},
            "say": say,
            "danger": not readonly,
            "guard_desc": "" if readonly else f"执行命令：{command[:80]}",
        }

    if action in ("screenshot", "lock"):
        return {"action": action, "args": {}, "say": say, "danger": False}

    # chat 以及任何不认识的 action 一律降级为纯聊天
    return {"action": "chat", "say": say or "这个动作我暂时做不了", "danger": False}


def _confirm(message: str) -> dict:
    """配置/网络错误直接变成可朗读的提示，不再静默落到“这条指令我还没学会”。"""
    print(f"[AI 配置] {message}")
    return {"action": "chat", "say": message, "error": True}


def _http_error(provider_name: str, response) -> dict:
    code = int(getattr(response, "status_code", 0) or 0)
    detail = str(getattr(response, "text", "") or "").replace("\n", " ")[:180]
    print(f"[{provider_name} 错误] HTTP {code}: {detail}")
    if code in (401, 403):
        return _confirm(f"AI 密钥被拒绝（HTTP {code}）：请检查这把 Key 是否属于当前服务商。")
    if code == 404:
        return _confirm("接口地址或模型名不对（HTTP 404）：请检查接口地址与模型名。")
    if code == 429:
        return _confirm("AI 接口请求太频繁（HTTP 429）：请稍后再试或检查账户额度。")
    if code >= 500:
        return _confirm(f"AI 服务端暂时不可用（HTTP {code}）：请稍后再试。")
    return _confirm(f"AI 接口返回错误（HTTP {code}）：请检查模型名、接口地址和账户额度。")


def query_llm_plan(command: str, config: dict, memory_context: str = "") -> dict:
    """根据配置的提供商与 API Key 调用大模型。"""
    provider_name = resolve_provider_name(config.get("ai_provider", "DeepSeek"))
    if not provider_name:
        return _confirm("当前 AI 服务商配置无法识别，请在左侧配置里重新选择服务商。")

    provider_info = LLM_PROVIDERS[provider_name]
    raw_key = (config.get("api_key") or "").strip() or os.environ.get("AI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    api_key = clean_api_key(raw_key)

    if not api_key:
        if raw_key:
            # 常见于把聊天内容/带空行的文字粘进了密钥框
            print(f"[AI 密钥格式不对] 密钥应是一串不含空格的字符, 当前 {len(raw_key)} 字符; 请在界面里重新粘贴")
            return _confirm("API Key 格式不对：应是一串不含空格和换行的可见字符，请重新粘贴。")
        return _confirm("还没有配置可用的 API Key，请在左侧“AI 大模型配置”里填写并保存。")

    model = str(config.get("ai_model") or "").strip() or provider_info["default_model"]
    if not model:
        return _confirm("当前服务商还没有填写模型名，请在配置里补充模型名。")

    api_url = str(config.get("ai_url") or "").strip() or provider_info["url"]
    if not api_url:
        return _confirm("当前服务商还没有配置接口地址，请在配置里补充接口地址。")
    if provider_info["type"] == "openai_compatible":
        api_url = normalize_openai_endpoint(api_url)
        if not api_url:
            return _confirm("接口地址不合法：请填写以 http:// 或 https:// 开头的地址。")

    try:
        # 1. OpenAI 兼容协议 (DeepSeek / 通义千问 / 智谱 / Kimi / OpenAI / 自定义)
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
                return _http_error(provider_name, r)

            data = r.json()
            content = data["choices"][0]["message"]["content"]
            raw_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

        # 2. Anthropic Claude 协议
        elif provider_info["type"] == "anthropic":
            system_content = SYSTEM_PROMPT
            if memory_context:
                system_content += "\n\n" + str(memory_context)
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "max_tokens": 1024,
                "system": system_content,
                "messages": [{"role": "user", "content": command}]
            }
            r = requests.post(api_url, headers=headers, json=payload, timeout=25)
            if r.status_code != 200:
                return _http_error(provider_name, r)
            data = r.json()
            blocks = data.get("content") or []
            raw_text = "\n".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict)).strip()
            if not raw_text:
                return _confirm("Claude 没有返回可用文本，请检查模型名和账户状态。")

        # 3. Google Gemini 协议
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
                return _http_error(provider_name, r)

            data = r.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return _confirm("Gemini 没有返回候选结果，请检查模型名和账户状态。")
            raw_text = candidates[0]["content"]["parts"][0]["text"].strip()

        else:
            return _confirm("当前 AI 服务商协议暂不支持，请改用 OpenAI 兼容或 Gemini。")

        # 解析模型返回的 JSON：部分兼容模型会在 JSON 前后加说明文字
        clean_text = re.sub(r"^```(json)?|```$", "", raw_text, flags=re.M).strip()
        start, end = clean_text.find("{"), clean_text.rfind("}")
        if start != -1 and end > start:
            clean_text = clean_text[start:end + 1]
        plan = json.loads(clean_text)

        # 出口统一校验: 白名单动作 + 参数合法性
        return sanitize_plan(plan)

    except requests.exceptions.Timeout:
        return _confirm("AI 接口响应超时，请稍后重试或检查网络。")
    except requests.exceptions.ConnectionError:
        return _confirm("连不上 AI 接口：请检查网络、接口地址或代理设置。")
    except json.JSONDecodeError:
        return _confirm("大模型返回的不是可执行 JSON，请确认该模型支持 JSON 输出。")
    except Exception as e:
        msg = str(e)
        # 不要把异常原文整个打出来: requests 的 InvalidHeader 会把 Bearer 后面那串带进日志
        if "header value" in msg or type(e).__name__ == "InvalidHeader":
            print("[大模型调用失败] 请求头不合法, 密钥里可能混入了空格/中文, 请重新粘贴")
            return _confirm("API Key 格式不对：请求头不合法，请重新粘贴一串不含空格的密钥。")
        print(f"[大模型调用失败] {type(e).__name__}: {msg[:160]}")
        return _confirm("AI 调用失败，请检查模型名、接口地址和网络后重试。")
