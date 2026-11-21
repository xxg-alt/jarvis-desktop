# -*- coding: utf-8 -*-
"""
贾维斯 v1 - Windows 桌面语音助手
唤醒词: 贾维斯 / 星光 (config.json 可改)
方式: 语音唤醒 + Ctrl+Alt+J 热键 + 文字模式兜底
大脑: 本地规则优先 (快、稳、免费); 规则不认识的指令, 有 Gemini 密钥时交给 Gemini 免费档理解
安全: 关机/重启/关闭软件等动作必须二次确认; Gemini 只能返回白名单动作
"""
import argparse
import ctypes
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import audio_device

BASE = Path(__file__).resolve().parent
DRY_RUN = False
# GUI 外壳回调（终端版保持 None）：文字、语音播放结束、麦克风音量和退出。
ON_SPEAK = None
ON_HEAR = None
ON_SPEAK_END = None
ON_AUDIO_LEVEL = None
ON_EXIT = None

# UI 状态总线 (胶囊/穿透层那套界面用; 终端模式没有也不影响)
try:
    import ui_bridge
except Exception:
    ui_bridge = None


def _ui_listening():
    """回到"正在听"状态 (识别循环的静息态)"""
    if ui_bridge is not None:
        try:
            ui_bridge.emit_state("LISTENING")
        except Exception:
            pass

# ---------- 配置 ----------
def load_env():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# 用户自己的设置 (密钥等) 放这里: 重装/重打包都不会被覆盖
USER_CFG_FILE = Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis" / "config.json"


def load_config():
    defaults = {
        "wake_words": ["贾维斯", "星光"],
        "hotkey": "ctrl+alt+j",
        "robot_voice": 0.7,
        "always_listen": True,
        "vision_enabled": True,
        "ai_provider": "DeepSeek",
        "api_key": "",
        "ai_model": "",
        "ai_url": "",
        # auto = 优先本地 whisper(中英文都准) -> 系统引擎 -> Vosk
        "stt_engine": "auto",
        "whisper_model": "small",
        "gemini_model": "gemini-flash-latest",
        "apps": {
            "记事本": "notepad.exe", "计算器": "calc.exe", "画图": "mspaint.exe",
            "文件管理器": "explorer.exe", "资源管理器": "explorer.exe",
            "命令提示符": "cmd.exe", "终端": "cmd.exe", "控制面板": "control.exe",
            "任务管理器": "taskmgr.exe", "浏览器": "msedge.exe", "谷歌浏览器": "chrome.exe",
            "word": "winword.exe", "excel": "excel.exe", "ppt": "powerpnt.exe",
            "微信": "WeChat.exe", "qq": "QQ.exe"
        },
        "app_exe_for_close": {
            "记事本": "notepad.exe", "计算器": "CalculatorApp.exe", "画图": "mspaint.exe",
            "浏览器": "msedge.exe", "谷歌浏览器": "chrome.exe",
            "word": "WINWORD.EXE", "excel": "EXCEL.EXE", "ppt": "POWERPNT.EXE",
            "微信": "WeChat.exe", "qq": "QQ.exe"
        },
        "sites": {
            "百度": "https://www.baidu.com", "哔哩哔哩": "https://www.bilibili.com",
            "b站": "https://www.bilibili.com", "youtube": "https://www.youtube.com",
            "油管": "https://www.youtube.com", "知乎": "https://www.zhihu.com",
            "微博": "https://weibo.com", "淘宝": "https://www.taobao.com",
            "京东": "https://www.jd.com", "github": "https://github.com"
        },
        "folders": {
            "桌面": str(Path.home() / "Desktop"), "文档": str(Path.home() / "Documents"),
            "下载": str(Path.home() / "Downloads"), "图片": str(Path.home() / "Pictures")
        }
    }
    # 先读程序自带配置, 再用用户配置覆盖 (用户配置放在 %APPDATA%\Jarvis, 不随安装包走)
    for cfg_file in (BASE / "config.json", USER_CFG_FILE):
        if not cfg_file.exists():
            continue
        try:
            user_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            for k, v in user_cfg.items():
                if isinstance(v, dict) and isinstance(defaults.get(k), dict):
                    defaults[k].update(v)
                else:
                    defaults[k] = v
        except Exception as e:
            print(f"[配置] {cfg_file} 读取失败, 跳过: {e}")
    return defaults

load_env()
CONFIG = load_config()
if ui_bridge is not None:
    try:
        ui_bridge.update_flags(
            always_listen=bool(CONFIG.get("always_listen", True)),
            vision_enabled=bool(CONFIG.get("vision_enabled", True)),
        )
    except Exception:
        pass
from jarvis_memory import JarvisMemory
MEMORY = JarvisMemory()

# Agent 任务拆解提示词: 只管"把目标拆成步骤", 不执行。
# 步骤格式沿用现有动作白名单, 由 sanitize_plan 出口统一校验。
AGENT_PLANNER_PROMPT = """你是桌面助手贾维斯的任务规划器。用户给你一个目标，你把它拆解成最少可行的动作步骤清单。
只能用这些动作: chat, open_app, open_file, open_url, web_search, file_manage(list/search/move/read), write_file, inspect_system, run_powershell(只读命令), ask_doc, screenshot。
写文件用 write_file: args={"name": "文件名.md或.txt或.docx", "content": "完整正文", "folder": "目标目录，可省略"}。content 必须是可以直接交付的成稿：用 Markdown 组织结构，写满真实内容，严禁留占位符、空条目、"（概述…）"这类待填空。
folder 只能填四类：工作区、桌面、文档、下载，可带子目录（如 "下载\\报告"）。用户没说存哪就省略 folder，默认写进工作区。用户要求存到别处（如 D 盘某个目录）时，folder 填那个完整路径原样，别改写。
各动作的参数必须齐全，缺参数的一律不要写进计划：
- file_manage search 要 {"action":"search","pattern":"*.pdf","folder":"下载"}；read 要 {"action":"read","name":"文件名"}；list 要 {"action":"list","folder":"下载"}
- ask_doc 要 {"query":"要查的问题"}
如果目标缺少必要素材（比如"整理上周工作"但不知道上周做了什么），就不要硬编成稿：整个计划只返回一个 chat 步骤，在 say 里向用户说明需要补充什么信息。
步骤总数不超过 8 步，能 2 步解决的绝不用 3 步。

【训练师补课：参数规格】每步参数必须按下表给全，缺了关键参数的步骤会被系统剔除或降级，等于白干：
- open_app 必须给 exe 和 name：{"exe": "WeChat.exe", "name": "微信"}。常用 exe：微信 WeChat.exe、记事本 notepad.exe、计算器 calc.exe、浏览器 msedge.exe、谷歌浏览器 chrome.exe、Word winword.exe、Excel excel.exe、文件管理器 explorer.exe、任务管理器 taskmgr.exe。只给 name 不给 exe 是最常见的错误，绝对不许。
- open_file 只给 name（文件名或标题，不要路径和后缀猜测）。
- inspect_system 的 mode 只能是 top_memory（查内存占用最高）、top_cpu（查 CPU 占用最高）、port（查端口，另给 target 端口号）。不要自创 mode 值。
- file_manage 的子动作写在 args.action 里：search 必须给 pattern（如 "*.pdf"）和 folder；list 给 folder；read 给 name；move 给 src（带目录，如 "下载\\报表.xlsx"）和 dst（目标目录名，如 "文档"）。
- volume 的 mode 只能 up/down/mute；run_powershell 只给一条只读命令。

【训练师补课：规划纪律】
- 不要加开场白式的 chat 步（“好的，我来处理”之类）：步骤从第一个真动作开始。chat 只在确实需要向用户问清或说明时用，要么单独一步结束计划，要么放在最后说明。
- 找某一类文件用 file_manage search + pattern，不要用 list 把整个目录列出来（list 没有过滤能力）。
- 写文件、移文件之后，计划里要带一步核对：对目标目录 list 或 search，确认东西真的到了。
- 关机用 shutdown、重启用 restart、关软件用 close_app，都不许用 run_powershell 拼命令代替；这类危险步永远放在计划最后一步。
- 计划阶段还不知道的信息（比如搜索结果里的具体链接）不许编造：先 web_search；后续步骤依赖搜索结果才能定的，就不要写那一步，改用一步 chat 说明“搜完请告诉我打开哪个”，或者只规划到搜索为止。
- “缺素材”只适用于需要用户本人真实记录、又无从得知的事（如他上周具体做了什么）；用户已给内容或主题明确的写作任务，按通用结构成稿，不要只返回 chat。
- 先查后写类任务（先查找/检查、再写文档）：write_file 的 content 只写清标题、结构和口径，不要编造将要查到的数据——执行时系统会把前面步骤的真实结果交给成稿环节，名单和数字都会照真实结果写进文件。

【示例】
目标：打开微信，再打开记事本
计划：{"goal": "打开微信和记事本", "steps": [{"action": "open_app", "args": {"exe": "WeChat.exe", "name": "微信"}, "say": "打开微信"}, {"action": "open_app", "args": {"exe": "notepad.exe", "name": "记事本"}, "say": "打开记事本"}]}
目标：把下载里的PDF都找出来，列个清单文档存到工作区
计划：{"goal": "整理下载中的PDF清单", "steps": [{"action": "file_manage", "args": {"action": "search", "pattern": "*.pdf", "folder": "下载"}, "say": "查找下载里的PDF"}, {"action": "write_file", "args": {"name": "PDF清单.md", "content": "# 下载中的PDF清单\\n\\n本文件由贾维斯在执行查找后生成，汇总下载目录中的 PDF 文件。"}, "say": "把清单写成文档存到工作区"}]}
目标：把下面这句话存成文档：今天复习了Unity的组件系统。然后关机
计划：{"goal": "存文档后关机", "steps": [{"action": "write_file", "args": {"name": "今日记录.md", "content": "今天复习了Unity的组件系统。"}, "say": "把这句话写成文档"}, {"action": "shutdown", "args": {}, "say": "关机"}]}

只输出 JSON，格式：
{"goal": "一句话目标", "steps": [{"action": "动作名", "args": {...}, "say": "这一步做什么的中文说明"}]}
不要输出其他文字。"""

def _agent_plan_from_llm(goal: str) -> dict:
    """调用大模型把目标拆成步骤清单。返回原始 JSON (未经校验)。"""
    from llm_brain import _provider_chat, _strip_json_text, _LlmError, SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": AGENT_PLANNER_PROMPT},
        {"role": "user", "content": str(goal or "").strip()},
    ]
    # 模型偶发吐坏 JSON (长计划尤其容易在中途损坏), 解析失败就重试, 最多 3 次
    last_err = None
    for attempt in range(3):
        try:
            raw = _provider_chat(CONFIG, messages, json_mode=(attempt < 2))
            if not str(raw or "").strip():
                continue
            plan = json.loads(_strip_json_text(raw))
            if isinstance(plan, dict) and plan.get("steps"):
                return plan
            last_err = "计划为空或格式不对"
        except _LlmError as e:
            last_err = e.message
        except Exception as e:
            last_err = str(e)
    print(f"[Agent 规划失败] 重试 3 次仍失败: {last_err}")
    return {}


# ---------- 语音输出 ----------
_tts_engine = None

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])(?![”’」』])")
_SENTENCE_QUOTE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;][”’」』])")

def _split_sentences(text, max_len=56):
    """把整段回答切成适合逐句显示/逐句播放的小句。

    规则：
    - 先按句号、问号、感叹号、分号切；
    - 单句过长时再按逗号/顿号/冒号切，避免一句占满整屏；
    - 过短的尾句并回上一句，避免“好。”“的。”这种碎片。
    """
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(raw) if p.strip()]
    parts = [
        q.strip()
        for p in parts
        for q in _SENTENCE_QUOTE_SPLIT_RE.split(p)
        if q.strip()
    ]
    out = []
    for part in parts:
        while len(part) > max_len:
            cut = max(
                part.rfind("，", 0, max_len),
                part.rfind(",", 0, max_len),
                part.rfind("、", 0, max_len),
                part.rfind("：", 0, max_len),
                part.rfind(":", 0, max_len),
            )
            if cut < max_len // 2:
                cut = max_len
            out.append(part[: cut + 1].strip())
            part = part[cut + 1 :].strip()
        if part:
            out.append(part)
    if len(out) > 1 and len(out[-1]) < 3:
        tail = out.pop()
        out[-1] = (out[-1] + tail).strip()
    return out


def _speak_one(text, on_start=None):
    text = str(text).strip()
    if not text:
        return

    # 机械音强度先解析，供自定义音频与云端 TTS 共用。
    # 注意：机械音开启时不再让 custom_voices 抢先播放，避免唤醒语或短句被
    # 未经处理的录音整段替换，造成“电流声时有时无”的错觉。
    robot_cfg = CONFIG.get("robot_voice", 0)
    if robot_cfg is True:
        robot_cfg = 0.7
    try:
        robot_cfg = float(robot_cfg)
    except (TypeError, ValueError):
        robot_cfg = 0.0

    # 1. 尝试匹配用户自定义音频包 (custom_voices/*.mp3, *.wav)
    # 只在自然音模式、且整句话就是预设短句时触发；不能按“包含关键词”匹配，
    # 否则普通回复里出现“我在”“好的”就会误播整段自定义音频。
    if robot_cfg <= 0:
        try:
            from custom_voice import try_play_custom_action
            action_map = {
                "我在": "wake",
                "好的": "ok",
                "没听清": "pardon",
                "已退出": "bye",
                "休息": "bye",
                "准备关机": "shutdown",
                "已截图": "screenshot"
            }
            normalized = re.sub(r"[\s，。！？、,.!?;；:：…~～·]+", "", text)
            for keyword, act_name in action_map.items():
                if normalized == keyword:
                    if on_start is not None:
                        try:
                            on_start()
                        except Exception:
                            pass
                    if try_play_custom_action(act_name):
                        return
        except Exception:
            pass

    # 2. 检查配置中的 tts 引擎: 支持 "edge" (超自然云端人声，支持云希/晓晓等角色)
    tts_engine_type = CONFIG.get("tts_engine", "edge")  # 默认使用高质量 edge 引擎
    if tts_engine_type == "edge":
        voice_choice = CONFIG.get("tts_voice", "云希")
        print(f"[TTS] {'机械音' if robot_cfg > 0 else '自然音'} · {voice_choice}")
        try:
            if robot_cfg > 0:
                from edge_speech import speak_edge_robot
                speak_edge_robot(text, voice_choice, robot_cfg, on_start=on_start)
            else:
                from edge_speech import speak_edge
                speak_edge(text, voice_choice, on_start=on_start)
            return
        except Exception as e:
            print(f"[Edge TTS 失败，降级回本地引擎] {e}")

    # 3. 本地引擎兜底 (pyttsx3)
    global _tts_engine
    try:
        import pyttsx3
        if _tts_engine is None:
            _tts_engine = pyttsx3.init()
            _tts_engine.setProperty("rate", 170)
            # 尽量选中文嗓音
            for v in _tts_engine.getProperty("voices"):
                if any(x in (v.name or "").lower() for x in ["huihui", "yaoyao", "chinese", "zh", "mandarin", "xiaoxiao"]):
                    _tts_engine.setProperty("voice", v.id)
                    break
        # 机器人音效处理 (如开启)
        robot_cfg = CONFIG.get("robot_voice", 0)
        if robot_cfg is True:
            robot_cfg = 0.7
        if robot_cfg and float(robot_cfg) > 0:
            try:
                from robot_voice import speak_robot
                if on_start is not None:
                    on_start()
                speak_robot(_tts_engine, text, float(robot_cfg))
                return
            except Exception as e:
                pass
        if on_start is not None:
            on_start()
        _tts_engine.say(text)
        _tts_engine.runAndWait()
    except Exception as e:
        print(f"[语音输出失败, 只显示文字] {e}")


def _speak(text):
    """整段连续 TTS + 句边界字幕；Edge 不可用时退回逐句播放。

    连续模式：
      Edge 流式合成 -> 首块音频到达即播放 -> 字幕按云端句边界时间戳切换
      -> 整段音频结束后字幕停留约 2.2 秒再清空
    这样句间不再有“合成一句、播一句、停 0.9 秒”的机械断裂。

    兼容模式（Edge 失败时）：
      仍然逐句显示 + 逐句播放，保证没有 Edge 时也能出声。

    重要：只有真正有音频在播时才把 is_speaking() 置为真；句间和最后字幕
    停留期放开麦克风，用户在贾维斯说完后立刻开口不会被整句丢掉。
    """
    global _SPEAKING_UNTIL, _CURRENT_STOP_EVENT
    text = str(text or "").strip()
    if not text:
        return
    print(f"贾维斯: {text}")

    sentences = _split_sentences(text)
    if not sentences:
        return

    if DRY_RUN:
        for i, sentence in enumerate(sentences):
            if ui_bridge is not None:
                ui_bridge.emit_subtitle(sentence, True)
            if i < len(sentences) - 1:
                if ui_bridge is not None:
                    ui_bridge.emit_subtitle("", True)
                time.sleep(1.2)
        time.sleep(1.8)
        if ui_bridge is not None:
            ui_bridge.emit_subtitle("", True)
        return

    # 解析机械音强度, 供连续流式和逐句兜底共用
    robot_cfg = CONFIG.get("robot_voice", 0)
    if robot_cfg is True:
        robot_cfg = 0.7
    try:
        robot_cfg = float(robot_cfg)
    except (TypeError, ValueError):
        robot_cfg = 0.0

    voice_choice = CONFIG.get("tts_voice", "云扬")
    tts_engine_type = str(CONFIG.get("tts_engine", "edge") or "edge").lower()
    streamed = False
    stream_started = False
    stream_interrupted = False

    # ── 首选：Edge 整段流式连续播放 ──
    if tts_engine_type == "edge":
        try:
            from edge_speech import speak_edge_streaming
        except Exception:
            speak_edge_streaming = None

        if speak_edge_streaming is not None:
            print(f"[TTS] 连续流式 · {'机械音' if robot_cfg > 0 else '自然音'} · {voice_choice}")
            stop_event = threading.Event()
            _CURRENT_STOP_EVENT = stop_event
            esc_handler = None
            try:
                import keyboard
                # 只在本次播报期间注册 Esc；按一下立即停下并回到监听
                esc_handler = keyboard.add_hotkey("esc", stop_event.set)
            except Exception:
                esc_handler = None

            last_stream_sentence = [None]

            def stream_start():
                global _SPEAKING_UNTIL
                nonlocal stream_started
                stream_started = True
                # 整段播放窗口; 结束后会收紧到 0.25 秒
                _SPEAKING_UNTIL = time.time() + max(8.0, min(600.0, len(text) * 0.5 + 15.0))
                first = sentences[0]
                last_stream_sentence[0] = first
                if ui_bridge is not None:
                    ui_bridge.emit_subtitle(first, True)
                if ON_SPEAK is not None:
                    try:
                        ON_SPEAK(first)
                    except Exception:
                        pass

            def stream_sentence(sentence):
                sentence = str(sentence or "").strip()
                if not sentence or sentence == last_stream_sentence[0]:
                    return
                last_stream_sentence[0] = sentence
                if ui_bridge is not None:
                    ui_bridge.emit_subtitle(sentence, True)
                if ON_SPEAK is not None:
                    try:
                        ON_SPEAK(sentence)
                    except Exception:
                        pass

            try:
                speak_edge_streaming(
                    text,
                    voice_choice,
                    on_start=stream_start,
                    on_sentence=stream_sentence,
                    robot_intensity=robot_cfg,
                    stop_event=stop_event,
                    on_level=_emit_audio_level,
                )
                streamed = True
                stream_interrupted = stop_event.is_set()
            except Exception as e:
                print(f"[连续 TTS 失败] {type(e).__name__}: {str(e)[:180]}")
                if stop_event.is_set():
                    # 用户在首块音频前按了 Esc：不要回退去重播整段
                    streamed = True
                    stream_started = True
                    stream_interrupted = True
                elif stream_started:
                    # 已经出过声：不再整段重播，避免重复；保持已显示字幕并尽快恢复监听
                    streamed = True
                    stream_interrupted = True
            finally:
                _CURRENT_STOP_EVENT = None
                if esc_handler is not None:
                    try:
                        import keyboard
                        keyboard.remove_hotkey(esc_handler)
                    except Exception:
                        pass

            if streamed:
                _SPEAKING_UNTIL = time.time() + (0.1 if stream_interrupted else 0.25)
                if not stream_interrupted:
                    # 最后一句字幕停留是视觉效果，不阻塞麦克风
                    time.sleep(2.2)
                if ui_bridge is not None:
                    try:
                        ui_bridge.emit_subtitle("", True)
                    except Exception:
                        pass
                return

    # ── 兜底：逐句播放（Edge 不可用或首块音频都没出来）──
    try:
        for i, sentence in enumerate(sentences):
            started = False

            def start(sentence=sentence):
                """音频真正开始播放时才显示字幕，避免云端合成延迟导致字幕提前。"""
                nonlocal started
                if started:
                    return
                started = True
                if ui_bridge is not None:
                    ui_bridge.emit_subtitle(sentence, True)
                if ON_SPEAK is not None:
                    try:
                        ON_SPEAK(sentence)
                    except Exception:
                        pass

            # 只在这一句的“合成 + 播放”窗口内屏蔽麦克风，留足余量防止尾音回声。
            _SPEAKING_UNTIL = time.time() + max(8.0, min(60.0, len(sentence) * 0.5 + 3.0))
            _speak_one(sentence, start)
            # 这一句音频已经结束，句间留白不再屏蔽，用户这时开口不会被整句丢掉。
            _SPEAKING_UNTIL = time.time() + 0.25
            # 极端情况下没有任何 TTS 能播放，也至少把这一句显示出来。
            if not started:
                start()
            if i < len(sentences) - 1:
                # 这一句已经念完：先让字幕淡出，留出一小段空白，再出现下一句。
                if ui_bridge is not None:
                    ui_bridge.emit_subtitle("", True)
                time.sleep(0.9)
        # 最后一句音频结束后，字幕停留 2.2 秒只是视觉效果，不应继续屏蔽麦克风。
        _SPEAKING_UNTIL = time.time() + 0.25
        time.sleep(2.2)
    finally:
        # 即使 TTS 中途失败，也不能把最后一句留在屏幕中央。
        _SPEAKING_UNTIL = time.time() + 0.25
        if ui_bridge is not None:
            try:
                ui_bridge.emit_subtitle("", True)
            except Exception:
                pass


def speak(text):
    """统一在真实播放结束后通知 GUI，能量球会随之停止回答动画。"""
    global _SPEAKING_UNTIL
    if not str(text).strip():
        return
    # 已经被更新指令顶掉的旧任务，哪怕算完了也不许出声。
    owner = getattr(_TL, "command_id", None)
    if owner is not None and not _command_is_current(owner):
        print(f"[丢弃过期播报] {text[:30]}")
        return
    # 播放期间标记"我在说话": 识别端据此丢弃自己声音的回声, 避免自问自答。
    # 这里只给一个初始窗口; _speak() 会按每一句的真实播放起止重新收紧/放开。
    _SPEAKING_UNTIL = time.time() + max(8.0, min(600.0, len(str(text)) * 0.5 + 15.0))
    try:
        _speak(text)
    finally:
        # 最后一段字幕停留不应继续屏蔽麦克风, 只留 0.25 秒尾音保护。
        _SPEAKING_UNTIL = time.time() + 0.25
        if ON_SPEAK_END is not None:
            try:
                ON_SPEAK_END()
            except Exception:
                pass
        # 播报结束 → 回到待命, 胶囊自动收起
        if ui_bridge is not None:
            try:
                ui_bridge.emit_state("IDLE")
            except Exception:
                pass


_SPEAKING_UNTIL = 0.0
_CURRENT_STOP_EVENT = None

# 异步工具只把结果放进队列，由语音主循环取出并播报。
# 每条指令/每次主动打断都会递增编号，旧任务因此失去回播资格。
_ASYNC_RESULTS = queue.Queue()
_ASYNC_ACTIONS = {"file_manage", "inspect_system", "run_powershell", "ask_doc"}
_DRAIN_LOCK = threading.Lock()
_ASYNC_DISPATCHER = None
_COMMAND_ID = 0
_COMMAND_ID_LOCK = threading.Lock()
# 同一时刻只处理一条指令：语音输入和文字输入同时到达时，后来的顶掉先来的，
# 不会各跑一条、各说一套。
_RUN_LOCK = threading.RLock()
_TL = threading.local()


def _new_command_id():
    global _COMMAND_ID
    with _COMMAND_ID_LOCK:
        _COMMAND_ID += 1
        return _COMMAND_ID


def _command_is_current(command_id):
    with _COMMAND_ID_LOCK:
        return command_id == _COMMAND_ID


def _cut_audio():
    """只掐断正在播放的声音，不动指令编号。"""
    global _SPEAKING_UNTIL, _CURRENT_STOP_EVENT
    _SPEAKING_UNTIL = 0.0
    if _CURRENT_STOP_EVENT is not None:
        try:
            _CURRENT_STOP_EVENT.set()
        except Exception:
            pass
        _CURRENT_STOP_EVENT = None


def stop_speaking():
    """强制掐断当前播报，并让尚未回播的异步工具结果过期。"""
    _new_command_id()
    _cut_audio()


def is_speaking():
    return time.time() < _SPEAKING_UNTIL

# ---------- 工具 ----------
_SAFE_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)


def is_safe_url(url):
    """只放行 http/https 网址。

    挡住 ms-msdt: / search-ms: / file:// / \\\\host 这类协议处理器 ——
    webbrowser.open 本质是 ShellExecute, 这些前缀会唤起系统组件, 属于代码执行面。
    """
    u = str(url or "").strip()
    return bool(_SAFE_URL_RE.match(u)) and len(u) <= 2048


def _run(cmd, **kw):
    if DRY_RUN:
        print(f"[dry-run] 将执行: {cmd}")
        return
    subprocess.Popen(cmd, shell=isinstance(cmd, str), **kw)

# ---------- 高危动作确认: 语音/文字/界面谁先答算谁 ----------
# 旧实现的两个坑:
#   1. confirm() 自己抢麦克风听 6 秒, 而主循环同时在收音 —— 两个识别器抢同一路输入;
#   2. 守卫在业务线程里阻塞等, "确认"却被主循环当成下一条指令, 守卫始终听不到,
#      最后超时按取消处理, 用户只看到"确认什么"。
# 新做法: 守卫把"待确认"登记成模块级状态; 指令入口在拿 _RUN_LOCK 之前先问一句
# "这是不是在回答守卫", 是就消费掉; 界面点击照旧生效, 两边谁先到算谁。
GUARD_TTL = 60.0
_GUARD_LOCK = threading.Lock()
_GUARD = {"token": 0, "desc": "", "event": None, "answer": "", "ts": 0.0, "active": False}
_GUARD_YES = ("确认", "确定", "确认执行", "确定执行", "好的", "好", "可以", "是的", "是",
              "对", "嗯", "同意", "执行", "关吧", "继续", "没问题", "行", "y", "yes")
_GUARD_NO = ("取消", "算了", "不用", "不要", "别", "不关", "停下", "停", "不做", "免了",
             "不", "n", "no")
_GUARD_PUNCT = re.compile(r"[。！？!?，,.、\s]")


def _guard_begin(desc):
    with _GUARD_LOCK:
        _GUARD["token"] += 1
        _GUARD.update(desc=str(desc or ""), event=threading.Event(),
                      answer="", ts=time.time(), active=True)
        return _GUARD["token"]


def _guard_end(token):
    with _GUARD_LOCK:
        if _GUARD["token"] == token:
            _GUARD["active"] = False
            _GUARD["event"] = None


def _guard_answer(text) -> bool:
    """指令入口的守卫拦截: 这句话若是在回答"待确认", 消费掉并返回 True。"""
    low = _GUARD_PUNCT.sub("", str(text or "")).lower()
    if not low:
        return False

    def _hit(words):
        # 精确命中, 或短句以该词开头 ("算了不要了" / "取消吧" 这类口语);
        # 长句不算确认 —— "确认一下微信关了吗" 是问题, 不是回答
        for w in words:
            if low == w or (len(low) <= 6 and low.startswith(w)):
                return True
        return False

    with _GUARD_LOCK:
        if not _GUARD.get("active"):
            return False
        if time.time() - float(_GUARD.get("ts") or 0) > GUARD_TTL:
            return False
        if _hit(_GUARD_NO):
            _GUARD["answer"] = "no"
            handled = True
        elif _hit(_GUARD_YES):
            _GUARD["answer"] = "yes"
            handled = True
        else:
            # 不是回答确认, 而是另一条指令: 按"取消"收尾, 这句话继续走正常流程。
            # 否则守卫会一直占着 _RUN_LOCK, 用户得等满 60 秒才能干别的事。
            _GUARD["answer"] = "no"
            handled = False
        event = _GUARD["event"]
    # 界面上的确认框也一起收掉, 免得它继续挂着等 60 秒
    if ui_bridge is not None:
        try:
            ui_bridge.resolve_guard(_GUARD["answer"] == "yes")
        except Exception:
            pass
    if event is not None:
        event.set()
    if handled and _GUARD["answer"] == "yes":
        speak("好，这就执行，Sir")
    return handled


def _guard_confirm(desc, timeout=GUARD_TTL) -> bool:
    """等一个确认: 界面点击 / 语音或文字回答, 谁先到算谁。"""
    token = _guard_begin(desc)
    speak(f"即将{desc}。确认就说「确认」，取消就说「取消」，Sir")
    ui_box = {"v": None}
    ui_thread = None

    def _ask_ui():
        try:
            ui_box["v"] = ui_bridge.request_guard_confirm(desc, timeout=timeout)
        except Exception:
            ui_box["v"] = None

    if ui_bridge is not None:
        ui_thread = threading.Thread(target=_ask_ui, name="jarvis-guard", daemon=True)
        ui_thread.start()

    deadline = time.time() + timeout
    verdict = None
    try:
        while time.time() < deadline:
            event = _GUARD.get("event")
            if event is not None and event.wait(0.15):
                verdict = _GUARD.get("answer") == "yes"
                break
            if ui_thread is not None and not ui_thread.is_alive():
                if ui_box["v"] is not None:      # 界面在跑: 点了确认或超时
                    verdict = bool(ui_box["v"])
                    break
                ui_thread = None                 # 没有界面: 继续等语音回答
    finally:
        _guard_end(token)
    if verdict is None:
        speak("等不到您的确认，这个操作我先不做了，Sir")
        return False
    return bool(verdict)


def confirm(question):
    """危险动作二次确认: 优先语音, 失败退回键盘输入"""
    speak(question + "。请说确认, 或者在窗口里输入 y")
    ans = ""
    # 先用系统识别引擎 (离线, 不再依赖谷歌), 拿不到再退回原路径
    try:
        import win_speech
        if win_speech.available():
            ans = win_speech.recognize_once(timeout=6, hotwords=["确认", "确定", "取消", "不要"])
    except Exception:
        ans = ""
    if not ans:
        try:
            import whisper_engine
            size = str(CONFIG.get("whisper_model", "small"))
            if whisper_engine.available(size):
                ans = whisper_engine.recognize_once(timeout=6, size=size)
        except Exception:
            ans = ""
    if not ans:
        try:
            ans = listen_once(timeout=6, phrase_limit=4)
        except Exception:
            pass
    if ans:
        return any(w in ans for w in ["确认", "是的", "好", "可以", "关吧", "嗯"])
    try:
        return input("确认吗? 输入 y 回车 = 确认, 其他 = 取消: ").strip().lower() == "y"
    except Exception:
        return False

# ---------- 规则解析: 指令 -> 动作计划 ----------
CN_NUM = {"零":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
def parse_number(s):
    if s is None: return None
    s = s.strip()
    if s.isdigit(): return int(s)
    if s in CN_NUM: return CN_NUM[s]
    if s.startswith("十") and len(s) == 2: return 10 + CN_NUM.get(s[1], 0)
    if s.endswith("十") and len(s) == 2: return CN_NUM.get(s[0], 0) * 10
    if "十" in s and len(s) == 3: return CN_NUM.get(s[0], 0) * 10 + CN_NUM.get(s[2], 0)
    return None

# ---------- 文档打开: 按文件名找, 而不是把整句话当文件名 ----------
# 只允许这些类型被"打开", exe/lnk/bat/ps1 一律不在此列, 避免语音指令变成执行程序
_DOC_EXTS = {".txt", ".md", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
             ".csv", ".rtf", ".wps", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".mp3",
             ".mp4", ".zip", ".7z"}
# 查找目录: 贾维斯工作区 + 桌面/文档/下载。工作区单独列出, 因为它已经不在"文档"里了。
# 名字驱动而非常量驱动: 工作区路径是运行时算的, 写成常量会在换盘/换机器时失效。
_DOC_PRUNE_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__", ".skills",
                   "site-packages", ".idea", "build", "dist"}
# 口语里的"打开一个名为XX的文本" -> XX
_DOC_FILLER_HEADS = re.compile(
    r"^(?:请|帮我|帮忙|麻烦|给我|把|打开|启动|运行|一个|一份|那张|这张|那个|这个|"
    r"名为|名字叫|名字是|叫做|标题为|标题是|叫)+"
)
# "打开下载里的报告.pdf" -> 报告.pdf
_DOC_FOLDER_HEADS = re.compile(r"^(?:桌面|下载|文档|图片|音乐|视频)(?:里|中|上|下面|里面)?(?:的)?")
_DOC_FILLER_TAILS = re.compile(r"(?:的)?(?:文本文件|文本|文件|文档|那个文件|那个文本|那份文件)+$")


def _doc_roots():
    """会被"打开文档"和"写文档"共用的根目录集合。

    两处必须共用同一份清单: 只进不出的路径等于写完之后自己都找不到,
    这种不对称 bug 比权限漏洞更难查 (用户只会说"它说不知道")。
    """
    try:
        ws = agent_workspace()
    except Exception:
        ws = Path.home() / "Documents" / "贾维斯工作区"
    return [ws, Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads"]


def _normalize_file_query(text):
    """把"打开下载里那个名为会议纪要的文本"这类口语归一成"会议纪要"。"""
    s = str(text or "").strip().strip("\"'“”‘’《》")
    for _ in range(4):   # 逐层剥: 打开 -> 一个 -> 名为 -> 的文本
        new_s = _DOC_FILLER_HEADS.sub("", s)
        if new_s == s:
            break
        s = new_s
    for _ in range(2):   # 再剥掉"下载里的""桌面上的"这类位置词
        new_s = _DOC_FOLDER_HEADS.sub("", s)
        if new_s == s:
            break
        s = new_s
    s = _DOC_FILLER_TAILS.sub("", s)
    s = s.strip(" ，,。.、　的")
    # 语音常把扩展名前的点念成"点": 你好点txt -> 你好.txt
    s = re.sub(r"点(txt|md|pdf|docx|doc|xlsx|xls|pptx|ppt|csv)$", r".\1", s, flags=re.I)
    return s.strip()


def _looks_like_document(name):
    """判断这个目标更像文档还是软件: 带文档扩展名, 或用户明说了文本/文件/文档。"""
    s = str(name or "").strip().lower()
    if Path(s).suffix in _DOC_EXTS:
        return True
    return any(w in s for w in ("文本", "文件", "文档", "表格", "图片", "表格"))


def _walk_docs(root):
    """"递归扫目录但跳过依赖/构建目录。

    项目就躺在桌面上, 而桌面是查找根目录之一: 不剪枝的话一次查找会把
    .venv 里几万个文件走一遍, 语音指令当场卡死几秒到几十秒。
    """
    root = Path(root)
    for cur, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d.lower() not in _DOC_PRUNE_DIRS]
        for fn in files:
            if Path(fn).suffix.lower() in _DOC_EXTS:
                yield Path(cur) / fn


def _document_candidates(query):
    """在贾维斯工作区/桌面/文档/下载里按文件名找文档, 精确命中优先, 同分按修改时间取最新。"""
    q = _normalize_file_query(query).lower()
    if not q:
        return []
    targets = {q}
    stem = Path(q).stem
    if stem:
        targets.add(stem)

    def _mtime(p):
        try:
            return p.stat().st_mtime
        except Exception:
            return 0.0

    exact, partial = [], []
    for root in _doc_roots():
        try:
            if not Path(root).exists():
                continue
            for p in _walk_docs(root):
                try:
                    fname, pstem = p.name.lower(), p.stem.lower()
                    if fname in targets or pstem in targets:
                        exact.append(p)
                    elif q in fname:
                        partial.append(p)
                except Exception:
                    continue
        except Exception:
            continue
    exact.sort(key=_mtime, reverse=True)
    partial.sort(key=_mtime, reverse=True)
    return exact + partial


def _open_document(name):
    """返回 (是否成功, 可直接朗读的中文)。找不到时给最接近的候选, 绝不猜着乱开。"""
    display = _normalize_file_query(name) or str(name or "").strip()
    cands = _document_candidates(name)
    if not cands:
        return False, f"我在桌面、文档和下载里没找到「{display}」，Sir"
    best = cands[0]
    try:
        os.startfile(str(best))
    except Exception as exc:
        print(f"[打开文档失败] {best}: {exc}")
        return False, f"找到了「{best.name}」，但它没有默认打开的程序，Sir"
    if len(cands) > 1:
        others = "、".join(p.name for p in cands[1:3])
        return True, f"已打开{best.name}；另外还有{others}，要换就说一声"
    return True, f"已打开{best.name}"


# ---------- Agent 地基 ----------
# 跨会话的任务对象: 一个 goal 拆成多个步骤, 每步都有状态可回填。
# 存放位置与 memory.json 同目录, 崩溃后能续跑; 也让"它刚才干了什么"可审计。
# 上限刻意很小: 一个任务最多 8 步, 防止模型一次生成几十步失控。
AGENT_TASKS_PATH = BASE / "agent_tasks.json"
AGENT_MAX_STEPS = 8
AGENT_STATES = ("draft", "approved", "running", "waiting_user", "done", "failed", "cancelled")

# 三级权限: 越界才问, 圈内直接干。agent 连续执行时每一步套这个格子,
# 计划审批已经放行的圈内操作不再逐条弹确认 —— 否则 agent 名存实亡。
AGENT_SCOPE_FREE = "free"        # 只读/开东西: list, read, open_*, search, chat, ask_doc, inspect
AGENT_SCOPE_NOTIFY = "notify"   # 落盘/系统层: write_file, move, volume, screenshot, run_powershell(只读)
AGENT_SCOPE_ASK = "ask"          # 危险: shutdown, restart, close_app, run_powershell(非只读)

_AGENT_FREE_ACTIONS = {"chat", "open_app", "open_file", "open_url", "open_folder",
                       "web_search", "search_file", "ask_doc", "screenshot", "lock",
                       "file_manage:read", "file_manage:list", "file_manage:search",
                       "inspect_system"}
_AGENT_NOTIFY_ACTIONS = {"write_file", "file_manage:move", "volume", "brightness",
                         "type_text", "press_key", "click"}


def agent_scope_of(action: str, args=None) -> str:
    """一个动作属于哪级权限。file_manage 按子动作细分, write_file 按目标目录细分。"""
    if action == "write_file":
        # 写工作区不打扰; 写桌面/文档/下载只提示; 写到白名单外才问 —— 目录决定权限
        return _write_target_scope(str((args or {}).get("folder") or ""))
    if action == "file_manage":
        sub = str((args or {}).get("action") or "")
        key = f"file_manage:{sub}"
        if key in _AGENT_FREE_ACTIONS:
            return AGENT_SCOPE_FREE
        if key in _AGENT_NOTIFY_ACTIONS:
            return AGENT_SCOPE_NOTIFY
        return AGENT_SCOPE_ASK
    if action in _AGENT_FREE_ACTIONS:
        return AGENT_SCOPE_FREE
    if action in _AGENT_NOTIFY_ACTIONS:
        return AGENT_SCOPE_NOTIFY
    return AGENT_SCOPE_ASK


AGENT_WS_FALLBACK = Path.home() / "Documents" / "贾维斯工作区"
_AGENT_WS_CACHE = {}


def agent_workspace() -> Path:
    """Agent 专用工作区: 落在项目根目录(仓库的上一层), 是落盘产物的默认去处。

    位置选择的三个约束:
    - 放仓库外面 (BASE.parent 而非 BASE): 产出是你的东西, 不该混进 git status;
    - 跟着项目走: 整个 jarvis-desktop-v1.2 文件夹拷到别的机器, 代码和产出一起走;
    - 保底: 打包安装到 Program Files 时那里只读, 探测失败退回 文档\贾维斯工作区,
      否则整套写文件能力会在别人的机器上直接废掉。
    """
    cached = _AGENT_WS_CACHE.get("path")
    if cached and Path(cached).parent.exists():
        return Path(cached)
    candidate = BASE.parent / "贾维斯工作区"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        print(f"[工作区不可写, 退回文档目录] {exc}")
        candidate = AGENT_WS_FALLBACK
        candidate.mkdir(parents=True, exist_ok=True)
    _AGENT_WS_CACHE["path"] = candidate
    return candidate


# ---------- 可写目录白名单 ----------
# 用户明确要求"能写到指定文件夹", 但全放开等于把能跑 PowerShell 的东西交给模型写任意路径。
# 分级依据是"这地方是不是用户自己的产出区":
#   工作区      -> free,   默认去处, 不打扰
#   桌面/文档/下载 -> notify, 写完告诉你落在哪
#   其它任何路径 -> ask,    停下来说清楚再动; 用户放行后仅对当前任务有效
_WRITE_ROOT_ALIASES = (
    ("workspace", ("贾维斯工作区", "agent工作区", "工作区", "你的工作区", "自己的工作区")),
    ("desktop", ("桌面",)),
    ("documents", ("文档区", "我的文档", "文档", "用户文档")),
    ("downloads", ("下载区", "下载")),
)
_WRITE_ROOT_LABELS = {"workspace": "贾维斯工作区", "desktop": "桌面",
                      "documents": "文档", "downloads": "下载"}
_WRITE_FOLDER_VERBS = re.compile(
    r"^(?:请|帮我|帮忙|麻烦|把|写到|写进|写到|存到|存进|放到|放在|保存到|保存在|保存|输出到|导出到|到)+"
)


def _write_root_map() -> dict:
    return {"workspace": agent_workspace(),
            "desktop": Path.home() / "Desktop",
            "documents": Path.home() / "Documents",
            "downloads": Path.home() / "Downloads"}


def _normalize_write_folder(hint) -> str:
    """把"写到下载里那个报告文件夹"归一成"下载\\报告文件夹"。"""
    s = str(hint or "").strip().strip("\"'“”‘’《》")
    s = s.replace("/", "\\")
    for _ in range(2):
        new_s = _WRITE_FOLDER_VERBS.sub("", s)
        if new_s == s:
            break
        s = new_s
    s = re.sub(r"(?:里面|里|中|上面|下面|上)$", "", s)
    return s.strip(" 的，,。、")


def _resolve_write_dir(hint):
    """解析目标目录 -> (Path, 显示名, 权限级, 错误话术)。

    空提示 -> 工作区; 白名单内(含子目录) -> notify; 绝对路径/其它 -> ask。
    纯解析, 不建目录: 权限没定之前不能有任何副作用。
    """
    raw = _normalize_write_folder(hint)
    roots = _write_root_map()
    if not raw:
        return roots["workspace"], _WRITE_ROOT_LABELS["workspace"], AGENT_SCOPE_FREE, None

    # 绝对路径 (D:\xxx 或 \\server\share): 直接算外部, 交给调用方决定放不放行
    if re.match(r"^[A-Za-z]:\\", raw) or raw.startswith("\\\\"):
        p = Path(raw)
        if any(part == ".." for part in p.parts):
            return None, raw, AGENT_SCOPE_ASK, "这个路径里有 .. ，我不能用"
        return p, raw, AGENT_SCOPE_ASK, None

    key, rest = None, raw
    for name, aliases in _WRITE_ROOT_ALIASES:
        for alias in aliases:
            if raw == alias:
                key, rest = name, ""
                break
            if raw.startswith(alias + "\\") or raw.startswith(alias):
                key, rest = name, raw[len(alias):]
                if rest[:1] not in ("", "\\"):
                    continue      # "下载备份" 不匹配 "下载"
                break
        if key:
            break

    if key is None:
        key, rest = "workspace", raw      # 裸目录名 -> 工作区下的子目录
    rest = rest.strip("\\").strip()
    if ".." in rest or ":" in rest:
        return None, raw, AGENT_SCOPE_ASK, "这个目录名里有非法字符，我不能用"

    root = roots[key]
    target = (root / rest) if rest else root
    try:
        resolved = target.resolve()
        root_resolved = root.resolve()
        if os.path.commonpath([str(resolved), str(root_resolved)]) != str(root_resolved):
            return None, raw, AGENT_SCOPE_ASK, "这个路径跳出了授权目录，我不能用"
    except Exception:
        return None, raw, AGENT_SCOPE_ASK, "这个路径解析不了，换一个试试"
    scope = AGENT_SCOPE_FREE if key == "workspace" else AGENT_SCOPE_NOTIFY
    label = _WRITE_ROOT_LABELS[key] + (f"\\{rest}" if rest else "")
    return resolved, label, scope, None


def _write_target_scope(hint) -> str:
    return _resolve_write_dir(hint)[2]


_WRITE_ROOT_CANON = {"workspace": "工作区", "desktop": "桌面",
                     "documents": "文档", "downloads": "下载"}


def _folder_hint_from_text(text) -> str:
    """兜底: 模型忘了填 folder 时, 从用户原话里捞存放位置。

    为什么要这层: folder 完全靠模型自觉, 一句"写到桌面"被漏掉, 文件就静悄悄进了工作区,
    用户只会觉得"它又不听话"。所以写文档这个动作再补一道确定性解析, 不信模型也信原话。
    """
    s = str(text or "")
    for name, aliases in _WRITE_ROOT_ALIASES:
        for alias in aliases:
            idx = s.find(alias)
            if idx < 0:
                continue
            tail = s[idx + len(alias):]
            tail = re.sub(r"^(?:区|上面|上|里面|里|中|下面)", "", tail)
            tail = tail.lstrip("的\\/ ")
            tail = re.split(r"[，,。；;！？!?、\s]", tail)[0]
            tail = re.sub(r"(?:里面|里|中|上面|下面|上)$", "", tail)
            tail = tail.strip("的\\/ ")
            if Path(tail).suffix.lower() in _DOC_EXTS or len(tail) > 40:
                tail = ""          # 后面跟的是文件名, 不是目录
            return _WRITE_ROOT_CANON[name] + ("\\" + tail if tail else "")
    return ""


def _write_document(name: str, content: str, folder=None, allow_outside: bool = False) -> tuple:
    """把模型写好的正文落成文件, 默认进贾维斯工作区, 也可指定目录。返回 (是否成功, 话术)。

    - 名字走裸名校验, 不接受路径分隔符和命令元字符;
    - 目标目录必须先过白名单: 白名单外只有在 allow_outside(用户对当前任务放行)时才写;
    - .md/.txt 直接写; .docx 生成 .md 后用系统 Word COM 转一遍;
    - 同名不覆盖: 自动加序号, agent 重跑不会吃掉上一版。
    """
    from llm_brain import _SHELL_META_RE
    raw = str(name or "").strip().strip("\"'“”‘’")
    if not raw:
        return False, "要写的文件名是空的，Sir"
    if len(raw) > 200 or _SHELL_META_RE.search(raw) or "/" in raw or "\\" in raw:
        return False, "这个文件名我不能用，换一个普通的名字"
    # 可执行/脚本类型一律拒绝: voice 场景只生成文档, 不生成能跑的东西
    if Path(raw).suffix.lower() in (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js",
                                    ".py", ".lnk", ".msi", ".scr", ".com", ".jar",
                                    ".reg", ".hta"):
        return False, "出于安全我只能写文档（md/txt/docx），不能生成可执行文件"
    if Path(raw).suffix.lower() not in (".md", ".txt", ".docx"):
        raw += ".md"   # 没写扩展名的一律当 Markdown

    text = str(content or "").strip()
    if not text:
        return False, "要写入的内容是空的，Sir"

    target_dir, label, scope, err = _resolve_write_dir(folder)
    if err:
        return False, err
    if scope == AGENT_SCOPE_ASK and not allow_outside:
        return False, (f"「{label}」不在我的授权目录里（只有工作区、桌面、文档、下载）。"
                       f"要写到那儿, 请先说「做个任务, 写到 {label}」, 我确认后再动")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"[建目录失败] {target_dir}: {exc}")
        return False, f"那个目录建不出来：{exc}"

    def _avoid_collision(base: str, suffix: str) -> Path:
        target = target_dir / (base + suffix)
        n = 1
        while target.exists():
            target = target_dir / f"{base}-{n}{suffix}"
            n += 1
        return target

    stem = Path(raw).stem
    want_docx = Path(raw).suffix.lower() == ".docx"
    try:
        if want_docx:
            # 先落 Markdown 保住内容, 再转 Word; 转换失败也不丢稿
            md_path = _avoid_collision(stem, ".md")
            md_path.write_text(text, encoding="utf-8")
            converted = _md_to_docx(md_path)
            if converted:
                return True, f"已写好 Word 文档{converted.name}，放在{label}里"
            return True, f"已写好{md_path.name}（Word 不可用，先存了 Markdown 版），放在{label}里"
        target = _avoid_collision(stem, Path(raw).suffix)
        target.write_text(text, encoding="utf-8")
    except Exception as exc:
        print(f"[写文档失败] {raw}: {exc}")
        return False, f"写文件失败了：{exc}"

    say = f"已写好{target.name}，放在{label}里"
    try:
        os.startfile(str(target))   # 写完即开, 和 open_file 闭环
        say = f"已写好并打开{target.name}"
    except Exception:
        pass
    return True, say


def _md_to_docx(md_path: Path):
    """用系统装的 Word 把 .md 转成 .docx; 没装 Word 返回 None。"""
    try:
        import win32com.client
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(md_path))
        docx_path = md_path.with_suffix(".docx")
        doc.SaveAs2(str(docx_path), FileFormat=16)   # 16 = wdFormatXMLDocument
        doc.Close(False)
        word.Quit()
        return docx_path
    except Exception as exc:
        print(f"[Word 转换不可用, 保留 Markdown] {exc}")
        return None


def _agent_load_tasks() -> dict:
    try:
        if AGENT_TASKS_PATH.exists():
            data = json.loads(AGENT_TASKS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        print(f"[Agent] 任务记录读不出来, 从空开始: {exc}")
    return {"tasks": []}


def _agent_save_tasks(data: dict):
    try:
        AGENT_TASKS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[Agent] 任务记录写不进去: {exc}")


def agent_tasks_text() -> str:
    """给"任务进展"语音查询用的一句话汇总。"""
    data = _agent_load_tasks()
    tasks = [t for t in data.get("tasks", []) if t.get("state") in ("running", "waiting_user")]
    if not tasks:
        done = [t for t in data.get("tasks", []) if t.get("state") == "done"]
        if done:
            return f"当前没有进行中的任务; 最近完成过 {len(done)} 个, 说「任务清单」可以看历史。"
        return "当前没有任何任务记录, Sir。"
    lines = []
    for t in tasks[:3]:
        done_n = sum(1 for s in t.get("steps", []) if s.get("status") == "done")
        total = len(t.get("steps", []))
        if t.get("state") == "running":
            pending = [i for i, s in enumerate(t.get("steps", [])) if s.get("status") == "pending"]
            where = f", 正在第 {pending[0] + 1} 步" if pending else ""
            lines.append(f"「{t.get('goal', '')[:40]}」{done_n}/{total} 步{where}")
            continue
        waiting = ", 等您确认" if t.get("state") == "waiting_user" else ""
        lines.append(f"「{t.get('goal', '')[:40]}」{done_n}/{total} 步{waiting}")
    return "; ".join(lines)


def agent_draft_plan(goal: str, steps: list = None, config: dict = None) -> dict:
    """把一个目标拆成步骤清单存成待审批任务; steps 为空时调用大模型拆解。

    返回给 UI 的结构: {task, say} —— 展示用, 不执行。
    只有 agent_approve 之后才真正进入 running。
    """
    if steps is None:
        steps = _agent_plan_from_llm(goal).get("steps")
    if not isinstance(steps, list) or not steps:
        return {"error": "步骤清单是空的, 没法立项", "say": "这个任务我拆不出步骤, 您说说具体要做什么"}
    steps = [dict(s) for s in steps if isinstance(s, dict)][:AGENT_MAX_STEPS]
    if not steps:
        return {"error": "步骤格式不对", "say": "任务步骤的格式我读不懂, 您换个说法试试"}

    # 归一 + 预判权限: 审批界面要一眼看出哪几步会动系统
    # 同时拦"参数不全被静默降级成 chat": 这种步骤执行了也只是一句废话, 宁可剔除
    from llm_brain import sanitize_plan
    norm_steps, scopes, needs_ask, dropped, chats = [], [], False, [], []
    for s in steps:
        requested = str(s.get("action") or "chat")
        plan = sanitize_plan({**s, "action": requested, "args": s.get("args", {}),
                              "say": s.get("say", "")})
        action = plan.get("action", "chat")
        if requested != "chat" and action == "chat":
            dropped.append(f"{requested}←{plan.get('say') or '参数不合法'}")
            print(f"[Agent] 步骤参数不合法, 已剔除: {requested} {s.get('args')} -> {plan.get('say')}")
            continue
        if action == "chat":
            chats.append(str(plan.get("say") or s.get("say") or ""))
        scope = agent_scope_of(action, plan.get("args"))
        needs_ask = needs_ask or scope == AGENT_SCOPE_ASK
        scopes.append(scope)
        norm_steps.append({"action": action, "args": plan.get("args", {}),
                           "say": plan.get("say", "")})

    # 一步可执行的都没有: 不要把空任务立出来, 直接把规划器那句话回给用户
    if not norm_steps or all(s["action"] == "chat" for s in norm_steps):
        say = next((c for c in chats if c), "这个任务我拆不出能执行的步骤, 您说说更具体的做法")
        print(f"[Agent] 计划不可执行, 不立项: {dropped}")
        return {"error": "no_executable_step", "say": say}

    task = {
        "goal": str(goal or "")[:200],
        "state": "draft",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "steps": [{"action": s["action"], "args": s["args"], "say": s["say"],
                   "status": "pending", "scope": sc}
                  for s, sc in zip(norm_steps, scopes)],
    }
    if needs_ask:
        task["note"] = "计划里含高危步骤, 审批时会单独标出"
    if dropped:
        task["dropped"] = dropped[:5]
    data = _agent_load_tasks()
    data.setdefault("tasks", []).append(task)
    _agent_save_tasks(data)

    text = f"任务已立项待审批「{task['goal']}」, 共 {len(task['steps'])} 步"
    if needs_ask:
        text += ", 其中有需要您单独确认的高危步骤"
    if dropped:
        text += f"; 另有 {len(dropped)} 步参数不全已剔除"
    return {"task": task, "say": text}


# ---------- Agent 后台执行器 ----------
# 设计要点:
#   1. 任务跑在独立线程里, 不占 _RUN_LOCK —— 否则跑几分钟的 agent 会把语音循环锁死;
#   2. 播报不 worker 里直接喊, 只往 _AGENT_EVENTS 塞, 由主循环统一出口 (避免和普通指令抢话筒);
#   3. 步骤之间检查 stop/skip 标志: 正在跑的这一步无法打断 (比如一次 25 秒的接口调用),
#      但一步结束立刻停 —— 这是"插话停得住"的现实边界。
_AGENT_LOCK = threading.Lock()
_AGENT_RUN = {"thread": None, "task": None, "stop": False, "skip": False, "gen": 0}
_AGENT_EVENTS = queue.Queue()


def _agent_busy() -> bool:
    t = _AGENT_RUN.get("thread")
    return bool(t and t.is_alive())


def _agent_notify(text: str, spoken: bool = False):
    """进度只上 HUD 字幕 (不吵人); 要打断用户的提问/结论才进播报队列。"""
    text = str(text or "").strip()
    if not text:
        return
    if ui_bridge is not None:
        try:
            ui_bridge.emit_subtitle(text, False)
        except Exception:
            pass
    if spoken:
        _AGENT_EVENTS.put(text)


def _agent_persist(task: dict):
    """按 (goal, created) 身份把任务写回任务文件; 找不到就追加。"""
    data = _agent_load_tasks()
    tasks = data.get("tasks", [])
    ident = (task.get("goal"), task.get("created"))
    for i, t in enumerate(tasks):
        if (t.get("goal"), t.get("created")) == ident:
            tasks[i] = task
            break
    else:
        tasks.append(task)
    _agent_save_tasks({**data, "tasks": tasks})


def _agent_start(task: dict) -> dict:
    """把任务丢进后台执行器, 立即返回一句"开始执行"——调用方线程不阻塞。"""
    with _AGENT_LOCK:
        if _agent_busy():
            return {"say": "已经有一个任务在跑了, 说「停」可以先停掉它"}
        _AGENT_RUN["gen"] += 1
        gen = _AGENT_RUN["gen"]
        _AGENT_RUN["stop"] = False
        _AGENT_RUN["skip"] = False
        _AGENT_RUN["task"] = task
        thread = threading.Thread(target=_agent_loop, args=(task, gen),
                                  name="jarvis-agent", daemon=True)
        _AGENT_RUN["thread"] = thread
        thread.start()
    total = len(task.get("steps", []))
    return {"say": f"开始执行任务「{task.get('goal', '')}」, 共 {total} 步。要中止就说「停」"}


def _agent_stop() -> bool:
    """请求停止。返回是否真的打断了一个在跑的任务。"""
    with _AGENT_LOCK:
        if not _agent_busy():
            return False
        _AGENT_RUN["stop"] = True
        _AGENT_RUN["gen"] += 1
        task = _AGENT_RUN.get("task") or {}
        if task:
            task["state"] = "cancelled"
            _agent_persist(task)
    return True


def _agent_skip() -> bool:
    with _AGENT_LOCK:
        if not _agent_busy():
            return False
        _AGENT_RUN["skip"] = True
    return True


def _agent_generation_changed(gen: int) -> bool:
    return _AGENT_RUN.get("gen") != gen


def _tool_data_text(action, res):
    """把工具的结构化结果压成一段可传递的文本, 供后续步骤 (尤其写文件) 使用。

    _perform_plan 平时只留下一句口头 summary, 具体名单/数字会丢;
    这里把 data 里的 items 全量列出 (最多 50 项), 让"先查后写"能写对。
    """
    data = res.get("data") or {}
    items = data.get("items") or []
    if items:
        names = []
        for item in items[:50]:
            if item.get("path"):
                names.append(Path(str(item["path"])).name)
            elif item.get("name"):
                extra = f"({item['memory_mb']}MB)" if item.get("memory_mb") else ""
                names.append(f"{item['name']}{extra}")
        if names:
            return f"共 {len(items)} 项: " + "、".join(names)
    return ""


_AGENT_COMPOSE_SYSTEM = (
    "你是桌面助手贾维斯的文档成稿器。一个多步任务正在执行, 现在轮到写文件这一步。\n"
    "前面步骤已经拿到了真实结果 (名单、数字、进程名都在【前面步骤的真实结果】里)。\n"
    "你必须只用这些真实结果成稿: 名称和数字逐项照抄, 不许编造、不许用占位符、不许写“待补充”。\n"
    "按【规划时的草稿/要求】的结构和口径写, 用中文, Markdown 格式, 简洁完整。只输出文件正文, 不要解释。"
)


def _agent_compose_write(goal, step, results_text):
    """写文件步执行前, 用前面步骤的真实结果把 content 成稿。

    - content 里有 {{结果}} 占位符: 直接把结果原文填进去 (确定性, 不调模型);
    - 否则调大模型按真实结果重写; 失败就退回原草稿, 绝不阻塞执行。
    """
    args = step.get("args", {}) or {}
    draft = str(args.get("content") or "")
    if "{{结果}}" in draft or "{{上一步结果}}" in draft:
        return draft.replace("{{上一步结果}}", results_text).replace("{{结果}}", results_text)
    try:
        from llm_brain import _provider_chat
        user = (f"【任务目标】{goal}\n"
                f"【要写的文件】{args.get('name', '文档')}\n"
                f"【规划时的草稿/要求】\n{draft}\n"
                f"【前面步骤的真实结果】\n{results_text}\n"
                "请写出最终文件正文。")
        text = _provider_chat(CONFIG, [
            {"role": "system", "content": _AGENT_COMPOSE_SYSTEM},
            {"role": "user", "content": user},
        ], max_tokens=900)
        text = str(text or "").strip()
        return text or draft
    except Exception as exc:
        print(f"[Agent 成稿失败, 用原草稿] {exc}")
        return draft


def _agent_loop(task: dict, gen: int):
    """后台执行循环。每步之间检查 stop/skip, 遇 ask 步骤立刻挂起等确认。"""
    steps = task.get("steps", [])
    total = len(steps)
    task["state"] = "running"
    _agent_persist(task)
    if ui_bridge is not None:
        try: ui_bridge.emit_state("ACTING")
        except Exception: pass

    results_log = []
    try:
        for i, step in enumerate(steps):
            if _agent_generation_changed(gen) or _AGENT_RUN.get("stop"):
                task["state"] = "cancelled"
                _agent_persist(task)
                _agent_notify("任务已停止, Sir", spoken=True)
                return
            if step.get("status") != "pending":
                continue

            if step.get("scope") == AGENT_SCOPE_ASK:
                task["state"] = "waiting_user"
                step["status"] = "waiting"
                _agent_persist(task)
                _agent_notify(
                    f"任务「{task.get('goal', '')}」第 {i + 1}/{total} 步要动系统: "
                    f"{step.get('say') or step.get('action')}。说「继续任务」放行, 说「停」中止",
                    spoken=True,
                )
                return

            if step.get("action") == "chat":
                step["status"] = "done"
                _agent_persist(task)
                continue

            if step.get("action") == "write_file" and results_log:
                # 传结果: 写文件步先按前面步骤的真实结果成稿, 再执行
                composed = _agent_compose_write(task.get("goal", ""), step,
                                                "\n".join(results_log))
                if composed and composed != (step.get("args", {}) or {}).get("content"):
                    step.setdefault("args", {})["content"] = composed

            _agent_notify(f"第 {i + 1}/{total} 步 · {step.get('say') or step.get('action')}")
            _TL.action_ok = True
            # 让执行器知道"现在是 agent 在跑": 白名单外的写入只有在审批放行过的时候才允许
            _TL.agent_context = True
            _TL.agent_external_ok = bool(step.get("external_ok"))
            try:
                step["result"] = _perform_plan({"action": step.get("action"),
                                                "args": step.get("args", {}),
                                                "say": step.get("say", "")})
                step["status"] = "done" if bool(getattr(_TL, "action_ok", True)) else "failed"
            except Exception as exc:
                step["result"] = f"执行失败: {exc}"
                step["status"] = "failed"
            finally:
                _TL.agent_context = False
                _TL.agent_external_ok = False
            if step.get("status") == "done":
                entry = f"第{i + 1}步({step.get('say') or step.get('action')}): {step.get('result', '')}"
                extra = getattr(_TL, "last_tool_text", "")
                if extra:
                    entry += f" | 数据: {extra}"
                results_log.append(entry)
            _agent_persist(task)

            if _AGENT_RUN.get("skip"):
                with _AGENT_LOCK:
                    _AGENT_RUN["skip"] = False
                for rest in steps[i + 1:]:
                    if rest.get("status") == "pending":
                        rest["status"] = "skipped"
                task["state"] = "done"
                _agent_persist(task)
                _agent_notify("已跳过剩余步骤, 任务收尾", spoken=True)
                return

        failed = [i for i, s in enumerate(steps) if s.get("status") == "failed"]
        done_n = sum(1 for s in steps if s.get("status") == "done")
        task["state"] = "failed" if failed and done_n == 0 else "done"
        _agent_persist(task)
        if failed:
            _agent_notify(f"任务「{task.get('goal', '')}」结束, 第 "
                          f"{'、'.join(str(i + 1) for i in failed)} 步没有成功", spoken=True)
        else:
            _agent_notify(f"任务「{task.get('goal', '')}」已完成, 共 {done_n} 步", spoken=True)
    except Exception as exc:
        print(f"[Agent 执行异常] {exc}")
        task["state"] = "failed"
        _agent_persist(task)
        _agent_notify(f"任务执行出错了: {exc}", spoken=True)
    finally:
        if _AGENT_RUN.get("task") is task:
            _AGENT_RUN["task"] = None
        if ui_bridge is not None:
            try: ui_bridge.emit_state("IDLE")
            except Exception: pass


def agent_approve(index: int = -1) -> dict:
    """把最新的 draft 任务交给后台执行器, 立即返回, 不阻塞指令线程。"""
    data = _agent_load_tasks()
    tasks = data.get("tasks", [])
    drafts = [i for i, t in enumerate(tasks) if t.get("state") == "draft"]
    if not drafts:
        return {"say": "没有待审批的任务, 先说「帮我做某某」让它立项"}
    task = tasks[drafts[-1]]
    task["state"] = "approved"
    _agent_save_tasks(data)
    return _agent_start(task)


def agent_continue() -> dict:
    """waiting_user 的任务放行当前 ask 步骤, 交给后台执行器续跑。"""
    data = _agent_load_tasks()
    for task in data.get("tasks", []):
        if task.get("state") != "waiting_user":
            continue
        task["state"] = "approved"
        for step in task.get("steps", []):
            if step.get("status") == "waiting":
                # 高危步骤既然被用户点名放行, 降级为 notify (留痕但不逐条弹确认)
                was_ask = step.get("scope") == AGENT_SCOPE_ASK
                step["status"] = "pending"
                step["scope"] = AGENT_SCOPE_NOTIFY
                if was_ask and step.get("action") == "write_file":
                    # 写白名单外目录这一步已被用户放行, 只对本次执行有效
                    step["external_ok"] = True
        _agent_save_tasks(data)
        return _agent_start(task)
    return {"say": "没有在等确认的任务"}


def agent_cancel() -> dict:
    """作废所有未完成任务; 在跑的那个会立刻请求停止。"""
    _agent_stop()
    data = _agent_load_tasks()
    changed = False
    for task in data.get("tasks", []):
        if task.get("state") in ("draft", "approved", "running", "waiting_user"):
            task["state"] = "cancelled"
            changed = True
    if changed:
        _agent_save_tasks(data)
    return {"say": "任务已全部作废, Sir" if changed else "没有进行中的任务"}


def parse_command(text):
    """返回 plan dict 或 None (None = 规则不认识, 交给 Gemini)"""
    t = text.strip().lower().replace(" ", "")
    raw = text.strip()

    # 时间日期
    if any(w in t for w in ["几点", "现在时间", "今天几号", "今天星期几"]):
        now = datetime.datetime.now()
        say = f"现在是{now.month}月{now.day}日, {now.hour}点{now.minute}分"
        return {"action": "chat", "say": say}

    # 关机/重启 (危险, 执行时确认)
    if "取消关机" in t:
        return {"action": "cancel_shutdown", "say": "已取消关机计划"}
    if "关机" in t:
        return {"action": "shutdown", "say": "准备关机", "danger": True}
    if "重启" in t or "重新启动" in t:
        return {"action": "restart", "say": "准备重启", "danger": True}
    if "锁屏" in t or "锁定屏幕" in t:
        return {"action": "lock", "say": "已锁屏"}

    # 截图
    if "截图" in t or "截屏" in t:
        return {"action": "screenshot", "say": "已截图, 保存在图片文件夹的贾维斯截图里"}

    # 静音/音量
    if "静音" in t:
        return {"action": "volume", "args": {"mode": "mute"}, "say": "已静音"}
    m = re.search(r"音量(?:调到|设为|到)(\d+|[零一二两三四五六七八九十]+)", t)
    if "音量" in t and m:
        n = parse_number(m.group(1))
        if n is not None:
            return {"action": "volume", "args": {"mode": "set", "value": max(0, min(100, n))}, "say": f"音量调到{n}"}
    if "音量" in t and any(w in t for w in ["大", "高", "增加", "调高"]):
        return {"action": "volume", "args": {"mode": "up", "steps": 5}, "say": "音量调大了"}
    if "音量" in t and any(w in t for w in ["小", "低", "减少", "调低"]):
        return {"action": "volume", "args": {"mode": "down", "steps": 5}, "say": "音量调小了"}

    # 亮度
    m = re.search(r"亮度(?:调到|设为|到)(\d+)", t)
    if "亮度" in t and m:
        n = int(m.group(1))
        return {"action": "brightness", "args": {"value": max(10, min(100, n))}, "say": f"亮度调到{n}"}

    # 网页搜索: 搜索xxx / 百度xxx / 查一下xxx
    m = re.search(r"(?:搜索|百度|查一下|查查|搜一下)(.+)", raw)
    if m and "搜索" not in t[:2] or (t.startswith("搜索") or t.startswith("百度")):
        q = m.group(1).strip() if m else ""
        if q:
            return {"action": "web_search", "args": {"query": q}, "say": f"正在搜索{q}"}
    if t.startswith("搜索") or t.startswith("搜一下"):
        q = re.sub(r"^搜(索|一下)", "", raw).strip()
        if q:
            return {"action": "web_search", "args": {"query": q}, "say": f"正在搜索{q}"}

    # 读取文件内容: 读取/读一下/念一下 + 带扩展名的文件名 (本地直通, 不必等大模型)
    m = re.search(r"(?:读取|读一下|读下|念一下|朗读)(.+)", raw)
    if m:
        fm = re.search(
            r"([\w\u4e00-\u9fff\-（）()\[\] ]+[.．]?(?:txt|md|pdf|docx))", m.group(1), re.I
        )
        if fm:
            fname = re.sub(r"^(?:名为|叫做|叫)", "", fm.group(1).strip())
            # 语音常把扩展名前的点说成“点”: 你好点txt -> 你好.txt
            dot = re.match(r"^(.+?)点(txt|md|pdf|docx)$", fname, re.I)
            if dot:
                fname = f"{dot.group(1)}.{dot.group(2)}"
            return {
                "action": "file_manage",
                "args": {"action": "read", "name": fname},
                "say": f"正在读取{fname}",
                "status_say": "正在读取文件",
                "danger": False,
            }

    # 打开网站
    for name, url in CONFIG["sites"].items():
        if f"打开{name}" in t or f"上{name}" in t:
            return {"action": "open_url", "args": {"url": url}, "say": f"已打开{name}"}

    # 打开文件夹 (必须整句就是"打开X(文件夹)", 否则"打开下载里的报告.pdf"会被误判成打开文件夹)
    for name, path in CONFIG["folders"].items():
        keys = (f"打开{name}", f"打开{name}文件夹", f"打开{name}目录", f"打开我的{name}")
        if t in keys:
            return {"action": "open_folder", "args": {"path": path}, "say": f"已打开{name}文件夹"}

    # 我能打开哪些软件 (从桌面/开始菜单的快捷方式现场扫描)
    if any(w in t for w in ["能打开哪些", "能开哪些", "有哪些软件", "有什么软件", "能开什么", "软件清单"]):
        try:
            from app_index import all_apps
            names = all_apps(desktop_only=True)
            head = "、".join(names[:12])
            say = f"我桌面上能找到{len(names)}个软件, 比如{head}。说「打开」加名字就行"
        except Exception as e:
            say = "暂时读不到软件清单"
        return {"action": "chat", "say": say}

    # 打开软件 / 打开文档
    m = re.search(r"(?:打开|启动|运行)(.+)", raw)
    if m:
        target = m.group(1).strip()
        tl = target.lower().replace(" ", "")
        for name, exe in CONFIG["apps"].items():
            if name in tl or tl in name:
                return {"action": "open_app", "args": {"exe": exe, "name": name}, "say": f"已打开{name}"}
        # 说的是文本/表格/图片这类文件: 按文件名去找, 不要当成软件
        if _looks_like_document(target):
            fname = _normalize_file_query(target)
            return {"action": "open_file", "args": {"name": fname},
                    "say": f"正在找{fname}", "status_say": "正在查找文件", "danger": False}
        # 在桌面/文档里找同名文件
        return {"action": "search_file", "args": {"query": _normalize_file_query(target)},
                "say": f"正在找{_normalize_file_query(target)}"}

    # 关闭软件 (危险, 执行时确认)
    m = re.search(r"(?:关闭|关掉|退出)(.+)", raw)
    if m:
        target = m.group(1).strip().lower().replace(" ", "")
        for name, exe in CONFIG["app_exe_for_close"].items():
            if name in target or target in name:
                return {"action": "close_app", "args": {"exe": exe, "name": name}, "say": f"已关闭{name}", "danger": True}
        return {"action": "chat", "say": f"我还不知道怎么关闭{target}, 可以在配置里加上它"}

    # 输入文字: 输入xxx / 打字xxx
    m = re.search(r"(?:输入|打字|打出)(.+)", raw)
    if m:
        return {"action": "type_text", "args": {"text": m.group(1).strip()}, "say": "3秒后输入, 请先点好输入框"}

    # 按键
    key_map = {"回车": "enter", "确认键": "enter", "空格": "space", "删除": "backspace",
               "退格": "backspace", "esc": "esc", "退出键": "esc", "制表键": "tab"}
    for name, key in key_map.items():
        if t in [f"按{name}", f"按一下{name}", f"按下{name}"] or t == f"按{key}":
            return {"action": "press_key", "args": {"key": key}, "say": "好的"}

    # 鼠标点击
    if t in ["点击", "点一下", "单击", "双击"]:
        return {"action": "click", "args": {"double": t == "双击"}, "say": "好的"}

    return None

# ---------- Gemini 兜底 (免费档, 只返回白名单动作 JSON) ----------
def gemini_plan(command, memory_context=""):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        import requests
        prompt = (
            "你是桌面助手的大脑。把用户指令转成一个JSON动作, 只能用这些action: "
            "open_app{exe,name}, open_url{url}, web_search{query}, chat{}(把回答写进say), "
            "screenshot, lock, volume{mode:up/down/mute}。"
            "exe只能从这些里选: notepad.exe, calc.exe, mspaint.exe, explorer.exe, msedge.exe, chrome.exe, winword.exe, excel.exe。"
            "只输出JSON, 格式 {\"action\":\"...\",\"args\":{...},\"say\":\"一句中文回复\"}, 不要输出其他文字。"
            + (f"\n{memory_context}" if memory_context else "")
            + "\n用户指令: " + command
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONFIG['gemini_model']}:generateContent"
        r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                          json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if r.status_code != 200:
            print(f"[Gemini] HTTP {r.status_code}: {r.text[:200]}")
            return None
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        txt = re.sub(r"^```(json)?|```$", "", txt, flags=re.M).strip()
        plan = json.loads(txt)
        # 和大模型走同一个出口关卡, 避免两套校验规则不一致
        from llm_brain import sanitize_plan
        return sanitize_plan(plan)
    except Exception as e:
        print(f"[Gemini失败] {e}")
        return None

# ---------- 执行动作 ----------
def _format_tool_say(action, res):
    """把系统工具的返回结果转成一句能直接念出来的话。

    工具自己的 summary 已经是中文口吻；这里只负责补上"具体是哪几个文件"，
    否则用户只听得到"共 12 项"，等于没告诉他。
    """
    summary = str(res.get("summary") or "").strip()
    data = res.get("data") or {}

    if action == "file_manage":
        items = data.get("items") or []
        base = summary.rstrip("。")
        if "folder" in data and items:
            names = []
            for item in items[:8]:
                if item.get("is_dir"):
                    names.append(f"{item.get('name')}\\")
                else:
                    names.append(f"{item.get('name')}({item.get('size_text')})")
            tail = f"等 {len(items)} 项" if len(items) > 8 else ""
            return f"{base}：{'、'.join(names)}{tail}。"
        if "pattern" in data and items:
            names = [Path(item.get("path", "")).name for item in items[:6]]
            return f"{base}：{'、'.join(names)}。"
        return summary

    return summary


_APP_SPLIT_RE = re.compile(r"(?:以及|还有|和|、|与|跟|＋|\+|,|，)")
_APP_BAD_IN_PART = ("打开", "启动", "运行", "帮我", "帮忙", "那个", "这个", "昨天", "明天",
                    "说的", "一下", "请", "顺便")
_MULTI_OPEN_HEAD_RE = re.compile(
    r"^(?:请|帮我|帮忙|麻烦|给我)?(?:同时|一起|分别|都|依次)?(?:打开|启动|运行|开)(?:一下|下)?"
)


def _split_app_targets(text):
    """"微信和QQ" -> ["微信", "QQ"]; 不是并列目标就返回 []。

    为什么要在执行层做: "同时打开A和B" 是并列目标, 但 open_app 只收一个名字,
    模型会把整串"微信和QQ"塞进 name, 启动器按整串去开始菜单找, 必然找不到 ——
    这就是"它说桌面没有这个应用"的直接原因。拆开这一层不依赖模型是否听话。
    """
    s = str(text or "").strip().strip("\"'“”‘’")
    if not s:
        return []
    parts = [p.strip(" 的。，,、") for p in _APP_SPLIT_RE.split(s)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return []
    # 长句 / 路径 / 带动作词或指代词的, 一律不拆: "帮我打开那个昨天说的微信和QQ" 不是并列目标
    if any(len(p) > 12 or "/" in p or "\\" in p or any(b in p for b in _APP_BAD_IN_PART)
           for p in parts):
        return []
    # 全是文档 或 全不是文档才拆; 混着(一个软件一个文档)交给单目标逻辑按文档兜底
    docs = [_looks_like_document(p) for p in parts]
    if any(docs) and not all(docs):
        return []
    return parts


def _multi_open_plans(command):
    """'同时打开微信和QQ' -> 两条 open_app 计划。

    本地规则 parse_command 遇到并列目标只会认第一个(实测 "同时打开微信和QQ" 只开了微信),
    所以并列句必须在这一层先截住, 既补全目标, 又不消耗模型 token。
    """
    s = str(command or "").strip().strip("。！？!?")
    if not s or not _APP_SPLIT_RE.search(s):
        return None
    compact = s.replace(" ", "")
    m = _MULTI_OPEN_HEAD_RE.match(compact)
    if not m:
        return None
    rest = compact[m.end():].strip(" 的，,、")
    parts = _split_app_targets(rest)
    if len(parts) < 2:
        return None
    return [{"action": "open_app", "args": {"exe": p, "name": p}, "say": f"打开{p}"}
            for p in parts]


def _multi_open_say(results):
    """把多目标启动结果拼成人话: 成功的并列, 失败的单独说清。"""
    ok = [name for name, good, _msg in results if good]
    bad = [(name, msg) for name, good, msg in results if not good]
    parts = []
    if ok:
        parts.append("已为您启动" + "、".join(ok))
    for name, msg in bad:
        hint = msg.split("。")[0] if msg else f"没找到{name}"
        parts.append(f"{name}没起来：{hint}")
    return "；".join(parts) if parts else "这几个我一个都没启动起来，Sir"


def _perform_plan(plan):
    """只执行动作并返回最终话术；异步工作线程不会在这里碰 UI。

    同时把"这一步到底成没成"记到线程本地 _TL.action_ok:
    话术是给人听的, 不能靠字符串猜成败 —— agent 步骤状态要读这个位。
    """
    action = plan.get("action")
    args = plan.get("args", {}) or {}
    say = plan.get("say", "好的")
    _TL.action_ok = True
    _TL.last_tool_text = ""

    try:
        if action == "chat":
            pass
        elif action == "open_app":
            from smart_launcher import smart_launch_app
            app_target = str(args.get("exe", "") or "")
            app_name = str(args.get("name", "") or app_target)
            # 并列目标先拆: "微信和QQ" 必须变成两次启动, 整串丢给启动器一定找不到
            names = _split_app_targets(app_name) or _split_app_targets(app_target)
            if len(names) > 1:
                results = []
                for nm in names:
                    if _looks_like_document(nm):
                        good, msg = _open_document(nm)
                    else:
                        good, msg = smart_launch_app(nm, nm)
                        if not good and _document_candidates(nm):
                            good, msg = _open_document(nm)
                    results.append((nm, good, msg))
                say = _multi_open_say(results)
                _TL.action_ok = all(good for _n, good, _m in results)
            # 模型常把"打开某某文本"塞进 open_app: 目标像文档就按文档开,
            # 否则找 exe; 找不到时再用文档兜一次, 别一上来就说"定位不到安装路径"
            elif _looks_like_document(app_target) or _looks_like_document(app_name):
                success, say = _open_document(app_name or app_target)
            else:
                success, say = smart_launch_app(app_target, app_name)
                if not success and _document_candidates(app_name or app_target):
                    success, say = _open_document(app_name or app_target)
        elif action == "open_file":
            success, say = _open_document(args.get("name") or args.get("exe") or "")
        elif action == "write_file":
            # 写文档: 默认落工作区, 也可指定 桌面/文档/下载 或子目录;
            # 越是白名单外的路径, 越必须由 agent 审批流程放行 (direct 指令由用户自己说的, 视为已授权)
            external_ok = (not bool(getattr(_TL, "agent_context", False)) or
                           bool(getattr(_TL, "agent_external_ok", False)))
            folder = args.get("folder") or _folder_hint_from_text(getattr(_TL, "user_text", ""))
            success, msg = _write_document(args.get("name"), args.get("content"),
                                           folder=folder,
                                           allow_outside=external_ok)
            say = msg
            _TL.action_ok = bool(success)
        elif action == "close_app":
            _run(["taskkill", "/im", args["exe"], "/f"])
        elif action == "open_url":
            url = str(args.get("url", "")).strip()
            if is_safe_url(url):
                webbrowser.open(url)
            else:
                say = "这个链接不是普通网址, 我只打开 http 或 https, Sir"
        elif action == "web_search":
            query = str(args.get("query", "")).strip()
            answered = ""
            try:
                from web_tools import search_and_answer
                answered = search_and_answer(query, CONFIG)
            except Exception as exc:
                print(f"[联网搜索失败] {exc}")
            if answered:
                say = answered
            else:
                from urllib.parse import quote
                webbrowser.open("https://www.baidu.com/s?wd=" + quote(query))
        elif action in ("file_manage", "inspect_system", "run_powershell"):
            # 第二步接入的三个真实系统工具：统一走 system_tools，结果再转成口吻
            try:
                from system_tools import file_manage, inspect_system, run_powershell
            except Exception as exc:
                print(f"[系统工具不可用] {exc}")
                say = "系统工具箱没加载上，这个动作暂时做不了。"
            else:
                if action == "file_manage":
                    res = file_manage(
                        action=args.get("action"),
                        src=args.get("src"),
                        dst=args.get("dst"),
                        pattern=args.get("pattern"),
                        folder=args.get("folder"),
                        name=args.get("name"),
                    )
                elif action == "inspect_system":
                    res = inspect_system(args.get("mode"), args.get("target"))
                else:
                    res = run_powershell(args.get("command"))

                if res.get("ok"):
                    say = _format_tool_say(action, res)
                    _TL.last_tool_text = _tool_data_text(action, res)
                else:
                    say = str(res.get("summary") or "这个操作没有成功。")
                    _TL.action_ok = False
        elif action == "ask_doc":
            # 文档问答: 本地语义检索最相关的 2~3 段, 再让大模型只依据原文回答
            query = str(args.get("query", "")).strip()
            try:
                import rag_engine
                hits = rag_engine.search_docs(query, top_k=3)
            except Exception as exc:
                print(f"[RAG] 文档检索失败: {exc}")
                hits = None
            if hits is None:
                say = "文档知识库还没准备好，这个暂时答不了，Sir。"
            elif not hits:
                say = "我在桌面、下载和文档里没翻到相关内容，Sir。"
            else:
                from llm_brain import query_llm_answer
                parts, names = [], []
                for i, hit in enumerate(hits):
                    fname = Path(hit["path"]).name
                    if fname not in names:
                        names.append(fname)
                    parts.append(f"【片段{i + 1} · {fname}】\n{hit['text']}")
                answer = query_llm_answer(query, "\n\n".join(parts), CONFIG)
                if any(name in answer for name in names):
                    say = answer
                else:
                    say = f"{answer}（出自{'、'.join(names[:2])}）"
        elif action == "open_folder":
            os.startfile(args["path"])  # Windows only
        elif action == "search_file":
            query = args.get("query", "")
            # 1) 先在桌面/开始菜单的软件清单里找: 支持中英文别名和拼音模糊
            try:
                from app_index import find_app, suggestions
                hit = find_app(query)
            except Exception as e:
                print(f"[应用清单不可用] {e}")
                hit, suggestions = None, None
            if hit:
                os.startfile(hit["path"])
                say = f"找到了, 已打开{hit['name']}"
            else:
                # 2) 再按文件名找文档: 归一化 + 精确优先, 多个候选开最新的
                if _document_candidates(query):
                    success, say = _open_document(query)
                else:
                    guess = suggestions(query) if suggestions else []
                    if guess:
                        say = f"没找到{query}, 你是不是想打开「{'」或「'.join(guess)}」?"
                    else:
                        say = f"在桌面、文档和下载里没找到{query}"
        elif action == "screenshot":
            import pyautogui
            folder = Path.home() / "Pictures" / "贾维斯截图"
            folder.mkdir(parents=True, exist_ok=True)
            fp = folder / f"截图_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
            vision_on = bool(CONFIG.get("vision_enabled", True))
            if ui_bridge is not None and vision_on:
                ui_bridge.emit_state("PERCEIVING")
            pyautogui.screenshot().save(str(fp))
            # 现阶段还没有视觉识别, 先用"当前活动窗口"当扫描目标框
            # 视觉感知关闭时仍会截图，但不再短暂切换感知状态或绘制扫描框。
            if ui_bridge is not None and vision_on:
                rect = None
                try:
                    from hud_overlay import active_window_rect
                    rect = active_window_rect()
                except Exception:
                    rect = None
                if rect:
                    ui_bridge.emit_hud_target("视觉扫描", *rect)
        elif action == "volume":
            import pyautogui
            mode = args.get("mode")
            if mode == "mute":
                pyautogui.press("volumemute")
            elif mode in ("up", "down"):
                key = "volumeup" if mode == "up" else "volumedown"
                for _ in range(int(args.get("steps", 5))):
                    pyautogui.press(key); time.sleep(0.05)
            elif mode == "set":
                # 近似: 先静音取消并按到0附近再往上加 (每按一次约2%)
                for _ in range(50): pyautogui.press("volumedown")
                for _ in range(int(args["value"] // 2)): pyautogui.press("volumeup")
        elif action == "brightness":
            ps = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{args['value']})"
            _run(["powershell", "-Command", ps])
        elif action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "shutdown":
            _run(["shutdown", "/s", "/t", "60"]); say = "60秒后关机, 说取消关机可以反悔"
        elif action == "restart":
            _run(["shutdown", "/r", "/t", "60"]); say = "60秒后重启, 说取消关机可以反悔"
        elif action == "cancel_shutdown":
            _run(["shutdown", "/a"])
        elif action == "type_text":
            import pyautogui, pyperclip
            speak(say); time.sleep(3)
            pyperclip.copy(args["text"]); pyautogui.hotkey("ctrl", "v")
            say = "已输入"
        elif action == "press_key":
            import pyautogui; pyautogui.press(args["key"])
        elif action == "click":
            import pyautogui
            if args.get("double"): pyautogui.doubleClick()
            else: pyautogui.click()
        else:
            say = "这个动作我暂时做不了"
            _TL.action_ok = False
    except Exception as e:
        say = f"执行失败了: {e}"
        _TL.action_ok = False
        print(f"[执行失败] {action}: {e}")
    return say


def _async_worker(plan, command_id, status_done):
    """后台只执行工具；等垫话结束后，把结果交给主循环，不直接播报或操作 UI。"""
    say = _perform_plan(plan)
    status_done.wait()
    if _command_is_current(command_id):
        _ASYNC_RESULTS.put((command_id, say))


def _drain_async_results():
    """回播最新任务结果；已打断或已有新指令的结果直接丢弃。

    语音主循环与图形界面的调度线程都会调用它，所以用非阻塞锁保证同一时刻
    只有一个线程在播报，不会两条结果叠在一起念。
    """
    if is_speaking():
        return
    if not _DRAIN_LOCK.acquire(blocking=False):
        return
    try:
        if is_speaking():
            return
        while True:
            try:
                command_id, say = _ASYNC_RESULTS.get_nowait()
            except queue.Empty:
                # 工具结果都播完了, 再看有没有 agent 后台任务的进度/提问/结论
                try:
                    agent_say = _AGENT_EVENTS.get_nowait()
                except queue.Empty:
                    return
                speak(agent_say)
                _ui_listening()
                return
            if not _command_is_current(command_id):
                continue
            speak(say)
            if _command_is_current(command_id):
                _ui_listening()
            return
    finally:
        _DRAIN_LOCK.release()


def _ensure_result_dispatcher():
    """图形界面/全屏 HUD 模式不走语音主循环，这里保底把结果念出来。"""
    global _ASYNC_DISPATCHER
    if _ASYNC_DISPATCHER is not None:
        return
    _ASYNC_DISPATCHER = threading.Thread(
        target=_dispatch_loop, name="jarvis-result-dispatch", daemon=True
    )
    _ASYNC_DISPATCHER.start()


def _dispatch_loop():
    while True:
        time.sleep(0.2)
        try:
            _drain_async_results()
        except Exception as e:
            print(f"[结果回播异常] {e}")


def execute_plan(plan, command_id=None):
    """执行动作；返回 True 表示系统工具已转入后台，最终结果稍后回播。"""
    if not plan:
        speak("这条指令我还没学会, 可以换个说法, 比如打开记事本、搜索天气、音量调大")
        return False
    action = plan.get("action")
    args = plan.get("args", {}) or {}
    say = plan.get("say", "好的")

    # 危险动作必须先在调用线程取得确认，后台线程绝不绕过安全护栏。
    if plan.get("danger"):
        if DRY_RUN:
            print(f"[dry-run] 危险动作 {action} 需要确认, 已跳过执行")
            return False
        desc = plan.get("guard_desc") or f"执行操作：{action}"
        # 语音/文字回答与界面点击并行等, 谁先到算谁 (详见 _guard_confirm 注释)
        verdict = _guard_confirm(desc)
        if not verdict:
            speak("好的，已取消操作，Sir。")
            return False

    if DRY_RUN:
        print(f"[dry-run] 动作={action} 参数={args} 回复={say}")
        return False

    status_say = str(plan.get("status_say") or "").strip()
    if action in _ASYNC_ACTIONS and status_say:
        if command_id is None:
            command_id = _new_command_id()
        if not _command_is_current(command_id):
            return False
        status_done = threading.Event()
        _ensure_result_dispatcher()
        threading.Thread(
            target=_async_worker,
            args=(plan, command_id, status_done),
            name=f"jarvis-tool-{action}",
            daemon=True,
        ).start()
        try:
            if _command_is_current(command_id):
                speak(status_say)
        finally:
            status_done.set()
        return True

    speak(_perform_plan(plan))
    return False


def handle_command(command):
    """指令入口：语音和文字共用，串行执行，后来的指令直接顶掉前一条。"""
    if not command or not command.strip():
        return
    command = command.strip()
    command_id = _new_command_id()
    # 原话留在 thread-local: 模型漏填 folder 时, 写文档要用它兜底解析存放位置
    _TL.user_text = command
    # 新指令一到先停掉上一条的声音；旧线程随后算出的结果都会因编号过期被丢弃。
    _cut_audio()
    # agent 任务在后台线程跑, 不占 _RUN_LOCK; 停/跳过这类插话要在这里直接吃掉,
    # 否则会被排队到任务结束才生效 (等于停不住)。
    if _agent_interrupt(command):
        return
    # 高危动作在等确认时, "确认/取消"必须先在这里被消费;
    # 否则它会排到 _RUN_LOCK 后面, 表现就是"我确认了, 它却问我确认什么"。
    if _guard_answer(command):
        return
    with _RUN_LOCK:
        _TL.command_id = command_id
        try:
            _handle_command_inner(command, command_id)
        finally:
            _TL.command_id = None


_AGENT_STOP_WORDS = ("停", "停下", "停一下", "别做了", "别干了", "停止", "中止", "打住")
_AGENT_SKIP_WORDS = ("跳过这步", "跳过这一步", "这步跳过", "跳过", "别做这步")


def _agent_interrupt(command: str):
    """agent 在跑时的插话入口: 返回 True 表示这句话已经被消费掉。

    必须在 _RUN_LOCK 之前调用 —— 任务占着锁跑几分钟时, 「停」也得能立刻生效。
    """
    if not _agent_busy():
        return False
    low = str(command or "").strip().lower().replace(" ", "")
    if low in _AGENT_SKIP_WORDS and _agent_skip():
        speak("这一步跳过, 接着往下走, Sir")
        return True
    if low in _AGENT_STOP_WORDS and _agent_stop():
        speak("好, 停了, Sir")
        return True
    return False


def _agent_control_command(command: str):
    """识别 agent 任务控制指令。返回 (kind, goal) 或 None。

    立项触发词刻意带"任务/安排/帮我做"这类字眼, 避免普通聊天误触发。
    """
    t = str(command or "").strip()
    low = t.lower().replace(" ", "")
    if any(w in low for w in ("任务进展", "任务状态", "任务清单")):
        return ("status", "")
    if low in ("继续任务", "放行任务", "任务继续", "确认继续"):
        return ("continue", "")
    if low in ("取消任务", "作废任务", "任务作废", "停止任务"):
        return ("cancel", "")
    if low in ("执行任务", "开始任务", "审批通过", "任务开始", "批准任务"):
        return ("approve", "")
    m = re.search(r"(?:帮我做|安排一个任务|安排任务|立项|帮我完成)(.+)", t)
    if m:
        goal = m.group(1).strip().strip("：:，。")
        goal = re.sub(r"^[个一场件份几]", "", goal).strip()  # "帮我做个演示" -> "演示"
        if goal:
            return ("draft", goal)
    return None


def _handle_command_inner(command, command_id):
    print(f"你: {command}")
    if ON_HEAR is not None:
        try: ON_HEAR(command)
        except Exception: pass
    if ui_bridge is not None:
        ui_bridge.emit_state("THINKING")
        ui_bridge.emit_subtitle(command, False)

    # 长期记忆控制（不进入大模型，不会误当成电脑操作）
    memory_control = MEMORY.control_command(command)
    if memory_control == "clear":
        MEMORY.clear()
        try:
            import rag_engine
            rag_engine.clear_mem()
        except Exception:
            pass
        try:
            import llm_brain
            llm_brain.reset_session()   # 历史留在窗口里会继续进前缀, 一起清掉
        except Exception:
            pass
        speak("长期记忆已清空, Sir")
        if ui_bridge is not None:
            ui_bridge.emit_state("IDLE")
        return
    if memory_control == "status":
        text = MEMORY.stats_text()
        try:
            import llm_brain
            text += " " + llm_brain.cache_stats_text()
        except Exception:
            pass
        speak(text)
        if ui_bridge is not None:
            ui_bridge.emit_state("IDLE")
        return
    note = MEMORY.extract_note(command)
    if note:
        MEMORY.add_note(note)
        speak(f"记住了: {note}")
        if ui_bridge is not None:
            ui_bridge.emit_state("IDLE")
        return

    # Agent 任务控制（本地规则, 不进大模型: 这些词必须 100% 可靠）
    agent_cmd = _agent_control_command(command)
    if agent_cmd:
        kind, goal = agent_cmd
        if kind == "status":
            speak(agent_tasks_text())
        elif kind == "approve":
            res = agent_approve()
            speak(res.get("say", ""))
        elif kind == "continue":
            res = agent_continue()
            speak(res.get("say", ""))
        elif kind == "cancel":
            speak(agent_cancel().get("say", ""))
        elif kind == "draft":
            ui_bridge.emit_state("ACTING") if ui_bridge is not None else None
            res = agent_draft_plan(goal)
            speak(res.get("say", "任务立项失败, 您说说具体要做什么"))
        if ui_bridge is not None:
            ui_bridge.emit_state("IDLE")
        return

    # 退出词：GUI 模式关闭整个应用；终端模式仍通过 SystemExit 退出主循环。
    if command in ["退出", "再见", "休息吧", "别听了"]:
        speak("好的, 我先休息了, 随时喊我")
        if ON_EXIT is not None:
            try:
                ON_EXIT()
            except Exception as e:
                print(f"[退出回调失败] {e}")
            return
        raise SystemExit

    # 并列打开: "同时打开微信和QQ"。本地规则只认第一个, 模型又会把整串当软件名,
    # 所以这里先按目标拆分, 逐个启动, 最后一句汇总(哪个没起来要点名)。
    multi = _multi_open_plans(command)
    if multi:
        if ui_bridge is not None:
            ui_bridge.emit_state("ACTING")
        results = []
        try:
            for p in multi:
                if not _command_is_current(command_id):
                    print("[指令已被新指令取代，丢弃]")
                    return
                msg = _perform_plan(p)
                results.append((p["args"]["name"], bool(getattr(_TL, "action_ok", True)), msg))
            say = _multi_open_say(results)
            speak(say)
            MEMORY.record_turn(command, {"action": "open_app",
                                         "args": {"names": [r[0] for r in results]}}, source="local")
        finally:
            if ui_bridge is not None:
                ui_bridge.emit_state("IDLE")
        return

    # 1. 本地规则优先 (最快、最稳、不消耗 Token)
    plan = parse_command(command)
    plan_source = "local" if plan is not None else ""

    # 2. 规则不认识的指令，交给配置好的 AI 大模型 (DeepSeek / 通义千问 / Gemini 等)
    if plan is None:
        memory_context = MEMORY.build_context(command)
        try:
            from llm_brain import query_llm_plan
            plan = query_llm_plan(command, CONFIG, memory_context=memory_context)
            if plan is not None:
                plan_source = "llm"
        except Exception as e:
            print(f"[AI 模块调用异常] {e}")

    # 3. 兼容原有 Gemini 兜底
    if plan is None:
        plan = gemini_plan(command, memory_context if 'memory_context' in locals() else "")
        if plan is not None:
            plan_source = "gemini"

    # 4. 用户对上一轮理解不满并给出正确指令时，保存纠正案例。
    previous = MEMORY.last_exchange()
    if plan is not None and previous.get("user") and MEMORY.is_correction(command):
        MEMORY.record_correction(previous["user"], command, plan)

    # 等大模型这段时间里可能又来了新指令，这一条已经作废，别再执行和播报
    if not _command_is_current(command_id):
        print("[指令已被新指令取代，丢弃]")
        return

    if ui_bridge is not None:
        ui_bridge.emit_state("ACTING")
    async_started = False
    try:
        async_started = execute_plan(plan, command_id=command_id)
    finally:
        MEMORY.record_turn(command, plan, source=plan_source or "unresolved")
        # 同步动作已经结束；异步工具仍由主循环等待最终结果。
        if ui_bridge is not None and not async_started:
            ui_bridge.emit_state("IDLE")

# ---------- 语音输入 ----------
def _emit_audio_level(data):
    """把 16 位单声道 PCM 的 RMS 音量归一化后发送给 GUI。"""
    if ON_AUDIO_LEVEL is None or not data:
        return
    try:
        import audioop
        rms = audioop.rms(data, 2)
        # 麦克风正常讲话的 RMS 通常远低于满量程，放大后再截断。
        level = min(1.0, (rms / 32768.0) * 8.0)
        ON_AUDIO_LEVEL(level)
        if ui_bridge is not None:
            ui_bridge.emit_audio_level(level)
    except Exception:
        pass


def listen_once(timeout=6, phrase_limit=6):
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.4)
        audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
    _emit_audio_level(audio.get_raw_data())
    return r.recognize_google(audio, language="zh-CN")


# ---------- 离线语音识别 (Vosk, 不依赖谷歌) ----------
MODEL_DIR = BASE / "models" / "vosk-model-small-cn-0.22"
_vosk_model = None


def _vosk_available():
    return MODEL_DIR.exists() and ((MODEL_DIR / "am").exists() or (MODEL_DIR / "conf").exists())


def _fuzzy_syl(s):
    """模糊化音节: shi/si, zhi/zi, chi/ci, ing/in, eng/en, ang/an 视为相同"""
    s = str(s).lower()
    s = s.replace("zh", "z").replace("ch", "c").replace("sh", "s")
    s = s.replace("ing", "in").replace("eng", "en").replace("ang", "an")
    return s


_FILLER_WORDS = {"嗯", "啊", "哦", "呃", "唉", "那个", "这个", "你好", "谢谢", "嗯嗯"}

# 说英文时喊 "Jarvis" 也要能唤醒 (whisper 听英文不会写成"贾维斯")
EN_WAKE_ALIASES = ("jarvis",)


def _is_meaningful(text):
    """过滤无意义的短语气词, 避免免唤醒模式被杂音触发"""
    t = text.replace(" ", "")
    return len(t) >= 2 and t not in _FILLER_WORDS


def _pinyin_squash(text):
    """把文字转成紧凑模糊拼音, 容忍同音错字 (贾维斯/假微丝/假为师)"""
    if not text:
        return ""
    try:
        from pypinyin import lazy_pinyin
        syl = "".join(_fuzzy_syl(x) for x in lazy_pinyin(text))
        return "".join(ch for ch in syl if "a" <= ch <= "z")
    except Exception:
        return "".join(ch for ch in text.lower() if "a" <= ch <= "z")


def _wake_hit(text):
    if not text:
        return False
    low = text.lower()
    # 英文场景: 喊 Jarvis 也算唤醒 (whisper 会把它认成英文而不是贾维斯)
    for a in EN_WAKE_ALIASES:
        if a in low:
            return True
    py = _pinyin_squash(text)
    for w in CONFIG["wake_words"]:
        if w in text:
            return True
        wp = _pinyin_squash(w)
        if wp and wp in py:
            return True
    return False


def _strip_wake(text):
    """去掉唤醒词后的剩余指令 (支持同音字)"""
    if not text:
        return ""
    for w in CONFIG["wake_words"]:
        if w in text:
            return text.split(w, 1)[1].strip(" ,，。")
    low = text.lower()
    for a in EN_WAKE_ALIASES:
        i = low.find(a)
        if i >= 0:
            rest = text[i + len(a):].strip(" ,，。.!?")
            if len(rest) > 1:
                return rest
    try:
        from pypinyin import lazy_pinyin
        chars = [c for c in text if not c.isspace()]
        syl = [_fuzzy_syl(x) for x in lazy_pinyin(chars)]
        for w in CONFIG["wake_words"]:
            ws = [_fuzzy_syl(x) for x in lazy_pinyin(w)]
            if not ws:
                continue
            for i in range(len(syl) - len(ws) + 1):
                if syl[i:i + len(ws)] == ws:
                    rest = "".join(chars[i + len(ws):]).strip(" ,，。")
                    return rest if len(rest) > 1 else ""
    except Exception:
        pass
    return ""


def _vosk_listen_command(rec, stream, timeout=6):
    """唤醒后录一条指令, 返回识别文本"""
    import json
    end = time.time() + timeout
    while time.time() < end:
        try:
            data = stream.read(4000, exception_on_overflow=False)
            _emit_audio_level(data)
        except Exception:
            continue
        if rec.AcceptWaveform(data):
            text = json.loads(rec.Result()).get("text", "").replace(" ", "")
            if text:
                return text
    return ""


def _wake_loop_vosk(hotkey_event):
    """本地离线的唤醒循环"""
    import json
    global _vosk_model
    import pyaudio
    import vosk

    print("正在加载离线语音模型...")
    if _vosk_model is None:
        _vosk_model = vosk.Model(str(MODEL_DIR))
    rec = vosk.KaldiRecognizer(_vosk_model, 16000)
    # 借用进程级 PortAudio 单例；不要 terminate，那会拆掉 TTS 输出/其它输入流
    stream = audio_device.open_input(format=pyaudio.paInt16, channels=1, rate=16000,
                                     input=True, frames_per_buffer=4000)
    print("正在听唤醒词(离线):", CONFIG["wake_words"])
    _ui_listening()
    if CONFIG.get("always_listen", True):
        print("免唤醒模式: 直接说指令即可 (面板可关闭)")
    follow_until = 0.0  # 一次命令后, 12 秒内可以继续追问, 不用再喊唤醒词
    # Vosk 会对每个 0.25 秒音频块跑 Kaldi；安静时先按 RMS 跳过模型，说话后再喂几块静音让端点收敛。
    import audioop
    rms_gate = max(80, int(os.environ.get("JARVIS_VOSK_RMS_GATE", "320")))
    gate_active = False
    gate_silence = 0

    def _execute(text):
        nonlocal follow_until
        text = text.strip()
        if not text:
            return
        try:
            handle_command(text)
        except SystemExit:
            raise
        finally:
            _ui_listening()
        follow_until = time.time() + 12

    def _after_wake():
        speak("我在")
        rec.Reset()
        cmd = _vosk_listen_command(rec, stream, timeout=10)
        if cmd:
            _execute(cmd)
        else:
            speak("没听清")

    try:
        while True:
            _drain_async_results()
            if hotkey_event.is_set():
                hotkey_event.clear()
                speak("我在")
                rec.Reset()
                cmd = _vosk_listen_command(rec, stream, timeout=8)
                if not cmd:
                    speak("没听清")
                else:
                    _execute(cmd)
                rec.Reset()
                continue
            try:
                data = stream.read(4000, exception_on_overflow=False)
            except Exception:
                continue
            try:
                block_rms = audioop.rms(data, 2)
            except Exception:
                block_rms = rms_gate
            if block_rms < rms_gate:
                if not gate_active:
                    continue
                gate_silence += 1
                if gate_silence > 3:
                    rec.Reset()
                    gate_active = False
                    gate_silence = 0
                    continue
            else:
                gate_active = True
                gate_silence = 0
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "").replace(" ", "")
                if not text:
                    continue
                if _wake_hit(text):
                    rest = _strip_wake(text)
                    rec.Reset()
                    if rest:
                        print(f"[唤醒] 听到: {text}")
                        _execute(rest)
                    else:
                        _after_wake()
                    rec.Reset()
                elif CONFIG.get("always_listen", True):
                    if _is_meaningful(text):
                        print(f"[直接执行] 听到: {text}")
                        _execute(text)
                    rec.Reset()
                elif time.time() < follow_until:
                    if _is_meaningful(text):
                        print(f"[继续] 听到: {text}")
                        _execute(text)
                    rec.Reset()
                continue
    finally:
        audio_device.release_stream(stream)


def _wake_loop_winrt(hotkey_event):
    """用 Windows 自带识别引擎听 (比 Vosk 小模型准得多, 且支持热词表)"""
    import queue as _queue
    import win_speech

    hotwords = list(CONFIG.get("wake_words", []))
    try:
        from app_index import all_apps
        hotwords += all_apps(desktop_only=True)
    except Exception:
        pass

    pending = _queue.Queue()
    stop = threading.Event()
    ready = threading.Event()
    failure = {}

    def _worker():
        try:
            win_speech.listen_continuous(
                pending.put, stop, hotwords,
                ignore_while=is_speaking, on_ready=ready.set,
            )
        except Exception as e:
            failure["error"] = e

    threading.Thread(target=_worker, daemon=True).start()
    if not ready.wait(6):
        stop.set()
        raise failure.get("error") or RuntimeError("系统语音引擎启动超时")

    print("系统语音已就绪, 唤醒词:", CONFIG["wake_words"])
    _ui_listening()
    if CONFIG.get("always_listen", True):
        print("免唤醒模式: 直接说指令即可")
    follow_until = 0.0

    def _run(text):
        nonlocal follow_until
        try:
            handle_command(text)
        except SystemExit:
            raise
        finally:
            _ui_listening()
        follow_until = time.time() + 12

    while True:
        _drain_async_results()
        if hotkey_event.is_set():
            hotkey_event.clear()
            speak("我在")
            follow_until = time.time() + 12
            continue
        try:
            text = pending.get(timeout=0.2).replace(" ", "")
        except _queue.Empty:
            continue
        if not text:
            continue
        # 用户打断（Barge-in）：如果贾维斯此时正在说话，用户开口说了有效指令，立刻掐断旧播报
        if is_speaking():
            stop_speaking()
            time.sleep(0.08)
        if _wake_hit(text):
            rest = _strip_wake(text)
            print(f"[唤醒] 听到: {text}")
            if rest:
                _run(rest)
            else:
                speak("我在")
                follow_until = time.time() + 12
        elif CONFIG.get("always_listen", True):
            if _is_meaningful(text):
                print(f"[直接执行] 听到: {text}")
                _run(text)
        elif time.time() < follow_until:
            if _is_meaningful(text):
                print(f"[继续] 听到: {text}")
                _run(text)


def _wake_loop_whisper(hotkey_event):
    """用本地 whisper 模型听 (中英文都准, 代价是每句话多等 1-2 秒)"""
    import queue as _queue
    import whisper_engine

    size = str(CONFIG.get("whisper_model", "small"))
    if not whisper_engine.ensure_model(size):
        raise RuntimeError(f"whisper 模型不可用: {whisper_engine.model_dir(size)}")

    pending = _queue.Queue()
    stop = threading.Event()
    ready = threading.Event()
    failure = {}
    worker_dead = threading.Event()

    def _on_mic_error(msg):
        """麦克风穷尽自愈手段后的最后一道通知：界面不能再假装在听。"""
        print(f"[语音] 麦克风故障: {msg}")
        if ui_bridge is not None:
            try:
                ui_bridge.emit_subtitle("麦克风异常，正在尝试恢复…", True)
            except Exception:
                pass

    def _worker():
        try:
            whisper_engine.listen_continuous(
                pending.put, stop,
                ignore_while=is_speaking, on_ready=ready.set,
                size=size, on_level=_emit_audio_level,
                # 本机主要是中文指令; 强制 zh 可避免 Whisper 把短句误判成英文
                # ("Hello"/"Uh-uh" 这类碎片), 英文单词仍会被中文模型尽量音译识别。
                language=str(CONFIG.get("whisper_language", "zh")),
                on_error=_on_mic_error,
            )
        except Exception as e:
            failure["error"] = e
        finally:
            worker_dead.set()

    threading.Thread(target=_worker, name="whisper-listen", daemon=True).start()
    if not ready.wait(30):
        stop.set()
        raise failure.get("error") or RuntimeError("whisper 启动超时")

    print(f"本地语音已就绪 (whisper-{size}), 唤醒词:", CONFIG["wake_words"])
    _ui_listening()
    if CONFIG.get("always_listen", True):
        print("免唤醒模式: 直接说指令即可")
    follow_until = 0.0

    def _run(text):
        nonlocal follow_until
        try:
            handle_command(text)
        except SystemExit:
            raise
        finally:
            _ui_listening()
        follow_until = time.time() + 12

    while True:
        _drain_async_results()
        if worker_dead.is_set():
            # 监听线程真的退出了。绝不能继续在这个循环里空转（那就是"卡死"的样子），
            # 直接起一个新的监听线程，让语音能力自愈。
            worker_dead.clear()
            err = failure.pop("error", None)
            print(f"[语音] 监听线程已退出({err!r})，自动重启…")
            ready.clear()
            threading.Thread(target=_worker, name="whisper-listen", daemon=True).start()
            if not ready.wait(10):
                print("[语音] 监听线程重启失败，请检查麦克风设备")
            else:
                print("[语音] 监听线程已恢复")
            continue
        if hotkey_event.is_set():
            hotkey_event.clear()
            speak("我在")
            follow_until = time.time() + 12
            continue
        try:
            text = pending.get(timeout=0.2).replace(" ", "")
        except _queue.Empty:
            continue
        if not text:
            continue
        # 用户打断（Barge-in）：如果贾维斯此时正在说话，用户开口说了有效指令，立刻掐断旧播报
        if is_speaking():
            stop_speaking()
            time.sleep(0.08)
        if _wake_hit(text):
            rest = _strip_wake(text)
            print(f"[唤醒] 听到: {text}")
            if rest:
                _run(rest)
            else:
                speak("我在")
                follow_until = time.time() + 12
        elif CONFIG.get("always_listen", True):
            if _is_meaningful(text):
                print(f"[直接执行] 听到: {text}")
                _run(text)
        elif time.time() < follow_until:
            if _is_meaningful(text):
                print(f"[继续] 听到: {text}")
                _run(text)


def wake_loop():
    import speech_recognition as sr
    speak("贾维斯已上线，随时待命")
    # RAG: 后台加载向量模型并增量建文档索引, 不阻塞启动
    try:
        import rag_engine
        rag_engine.startup()
    except Exception as e:
        print(f"[RAG] 启动预热失败: {e}")
    # 热键线程
    hotkey_event = threading.Event()
    try:
        import keyboard
        keyboard.add_hotkey(CONFIG["hotkey"], lambda: hotkey_event.set())
    except Exception as e:
        print(f"[热键不可用] {e}")

    # 识别引擎: auto = whisper(本地, 中英文都准) -> 系统自带 -> Vosk
    engine = str(CONFIG.get("stt_engine", "auto")).lower()

    if engine in ("auto", "whisper", "local"):
        try:
            import whisper_engine
            size = str(CONFIG.get("whisper_model", "small"))
            if whisper_engine.available(size):
                _wake_loop_whisper(hotkey_event)
                return
            if engine != "auto":
                print(f"[语音] 找不到 whisper 模型, 尝试自动下载 ({size})")
                _wake_loop_whisper(hotkey_event)
                return
            print("[语音] whisper 模型还没下载, 先用系统引擎")
        except Exception as e:
            print(f"[语音] whisper 引擎启动失败: {type(e).__name__}: {str(e)[:120]}")
            if engine != "auto":
                raise

    if engine in ("auto", "windows", "win", "system"):
        try:
            import win_speech
            if win_speech.available():
                _wake_loop_winrt(hotkey_event)
                return
            print("[系统语音] 这台机器没有中文识别引擎, 改用离线 Vosk")
        except Exception as e:
            msg = str(e)
            if "privacy" in msg.lower():
                print("[系统语音] 需要先打开系统设置: 设置 → 隐私和安全性 → 语音 → 打开「在线语音识别」")
            print(f"[系统语音] 启动失败, 改用离线 Vosk: {msg[:120]}")

    if _vosk_available():
        try:
            _wake_loop_vosk(hotkey_event)
            return
        except Exception as e:
            print(f"[离线语音异常, 改用在线识别] {e}")
    r = sr.Recognizer()
    try:
        mic = sr.Microphone()
    except Exception as e:
        print(f"[没有可用麦克风, 转文字模式] {e}")
        text_loop(); return
    with mic as source:
        r.adjust_for_ambient_noise(source, duration=0.8)
    print("正在听唤醒词:", CONFIG["wake_words"])
    while True:
        _drain_async_results()
        # 热键触发
        if hotkey_event.is_set():
            hotkey_event.clear()
            speak("我在")
            try: handle_command(listen_once())
            except SystemExit: raise
            except Exception: speak("没听清, 再说一次吧")
            continue
        try:
            with mic as source:
                audio = r.listen(source, timeout=2, phrase_time_limit=4)
                _emit_audio_level(audio.get_raw_data())
            heard = r.recognize_google(audio, language="zh-CN")
        except Exception:
            continue
        if not heard:
            continue
        for w in CONFIG["wake_words"]:
            if w in heard:
                rest = heard.split(w, 1)[1].strip(" ,，。")
                print(f"[唤醒] 听到: {heard}")
                if rest:
                    try: handle_command(rest)
                    except SystemExit: raise
                else:
                    speak("我在")
                    try: handle_command(listen_once())
                    except SystemExit: raise
                    except Exception: speak("没听清")
                break

def text_loop():
    print("文字模式: 输入指令回车执行, 输入 退出 结束。例如: 打开记事本 / 搜索明天天气 / 音量调大 / 截图")
    try:
        import rag_engine
        rag_engine.startup()
    except Exception as e:
        print(f"[RAG] 启动预热失败: {e}")
    commands = queue.Queue()

    def _read_input():
        while True:
            try:
                commands.put(input("> ").strip())
            except (EOFError, KeyboardInterrupt):
                commands.put(None)
                return

    # 输入放到旁边等待，主线程才能在工具完成时及时取结果回播。
    threading.Thread(target=_read_input, name="jarvis-text-input", daemon=True).start()
    while True:
        _drain_async_results()
        try:
            cmd = commands.get(timeout=0.2)
        except queue.Empty:
            continue
        except KeyboardInterrupt:
            break
        if cmd is None:
            break
        if not cmd:
            continue
        try:
            handle_command(cmd)
        except SystemExit:
            break

def main():
    global DRY_RUN
    ap = argparse.ArgumentParser(description="贾维斯 v1")
    ap.add_argument("--text", action="store_true", help="文字模式 (不用麦克风)")
    ap.add_argument("--once", type=str, default="", help="只执行一条指令后退出")
    ap.add_argument("--dry-run", action="store_true", help="只解析不真执行 (测试用)")
    a = ap.parse_args()
    DRY_RUN = a.dry_run
    if a.once:
        plan = parse_command(a.once)
        if plan is None: plan = gemini_plan(a.once)
        print(json.dumps(plan, ensure_ascii=False))
        execute_plan(plan); return
    if a.text:
        text_loop(); return
    try:
        wake_loop()
    except KeyboardInterrupt:
        print("已退出")
    except SystemExit:
        print("已退出")

if __name__ == "__main__":
    main()
