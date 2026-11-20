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
# GUI 外壳回调（终端版保持 None）：文字、语音播放结束和麦克风音量。
ON_SPEAK = None
ON_HEAR = None
ON_SPEAK_END = None
ON_AUDIO_LEVEL = None

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
_ASYNC_ACTIONS = {"file_manage", "inspect_system", "run_powershell"}
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

    # 打开网站
    for name, url in CONFIG["sites"].items():
        if f"打开{name}" in t or f"上{name}" in t:
            return {"action": "open_url", "args": {"url": url}, "say": f"已打开{name}"}

    # 打开文件夹
    for name, path in CONFIG["folders"].items():
        if f"打开{name}" in t:
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

    # 打开软件
    m = re.search(r"(?:打开|启动|运行)(.+)", raw)
    if m:
        target = m.group(1).strip()
        tl = target.lower().replace(" ", "")
        for name, exe in CONFIG["apps"].items():
            if name in tl or tl in name:
                return {"action": "open_app", "args": {"exe": exe, "name": name}, "say": f"已打开{name}"}
        # 在桌面/文档里找同名文件
        return {"action": "search_file", "args": {"query": target}, "say": f"正在找{target}"}

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


def _perform_plan(plan):
    """只执行动作并返回最终话术；异步工作线程不会在这里碰 UI。"""
    action = plan.get("action")
    args = plan.get("args", {}) or {}
    say = plan.get("say", "好的")

    try:
        if action == "chat":
            pass
        elif action == "open_app":
            from smart_launcher import smart_launch_app
            app_target = args.get("exe", "")
            app_name = args.get("name", app_target)
            success, msg = smart_launch_app(app_target, app_name)
            say = msg
        elif action == "close_app":
            _run(["taskkill", "/im", args["exe"], "/f"])
        elif action == "open_url":
            url = str(args.get("url", "")).strip()
            if is_safe_url(url):
                webbrowser.open(url)
            else:
                say = "这个链接不是普通网址, 我只打开 http 或 https, Sir"
        elif action == "web_search":
            from urllib.parse import quote
            webbrowser.open("https://www.baidu.com/s?wd=" + quote(str(args.get("query", ""))))
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
                    )
                elif action == "inspect_system":
                    res = inspect_system(args.get("mode"), args.get("target"))
                else:
                    res = run_powershell(args.get("command"))

                if res.get("ok"):
                    say = _format_tool_say(action, res)
                else:
                    say = str(res.get("summary") or "这个操作没有成功。")
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
                # 2) 再按文件名找文档 (精确子串)
                found = None
                for root_name in ["Desktop", "Documents", "Downloads"]:
                    root = Path.home() / root_name
                    if not root.exists(): continue
                    for p in root.rglob("*"):
                        if query.lower() in p.name.lower():
                            found = p; break
                    if found: break
                if found:
                    os.startfile(str(found))
                    say = f"找到了, 已打开{found.name}"
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
    except Exception as e:
        say = f"执行失败了: {e}"
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
        question = f"即将{desc}，确定要执行吗，Sir？"
        verdict = None
        if ui_bridge is not None:
            try:
                verdict = ui_bridge.request_guard_confirm(desc)
            except Exception:
                verdict = None
        if verdict is None:
            verdict = confirm(question)
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
    # 新指令一到先停掉上一条的声音；旧线程随后算出的结果都会因编号过期被丢弃。
    _cut_audio()
    with _RUN_LOCK:
        _TL.command_id = command_id
        try:
            _handle_command_inner(command, command_id)
        finally:
            _TL.command_id = None


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
        speak("长期记忆已清空, Sir")
        if ui_bridge is not None:
            ui_bridge.emit_state("IDLE")
        return
    if memory_control == "status":
        speak(MEMORY.stats_text())
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

    # 退出词
    if command in ["退出", "再见", "休息吧", "别听了"]:
        speak("好的, 我先休息了, 随时喊我")
        raise SystemExit

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
