# -*- coding: utf-8 -*-
"""faster-whisper 本地语音识别引擎 (完全离线)

和 win_speech.py 的区别:
- Vosk 小模型: 快但准头差, 尤其英文
- Windows 自带: 需要中文语言包 + 隐私开关, 只能听一种语言
- 本引擎: Whisper small 神经网络模型, 中英文都能听, 口音容忍度高

模型放在 models/faster-whisper-small/ (461MB), 下载一次之后永久离线。
语音数据只在本机内存里转文字, 不会发到任何服务器。

对外接口 (和 win_speech 保持一致的形状, 方便上层切换):
  available()                     -> 模型文件是否就位
  listen_continuous(on_text, ...) -> 后台线程持续听, 每说完一句回调一次
  recognize_once(timeout)         -> 听一句就返回
"""
import os
import threading
import time
from pathlib import Path

import audio_device

BASE = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
CHUNK = 480                 # 30ms
_PRE_ROLL = 12              # 保留 360ms 前置音频, 避免吃掉字头
_SIL_END = 24               # 连续 720ms 静音才视为说完 (0.54s 对正常换气太短)
_MIN_SPEECH = 12            # 至少 360ms 有效语音才算一句
_MAX_UTTER = 400            # 单句最长 12s, 到点强制切
_MAX_READ_FAILS = 8         # 连续读失败超过这次数就重建输入流

# 底噪门限与归一化上限:
# 旧版 0.9/peak 没有上限, 极轻的底噪会被放大几十上百倍, 等于把噪声喂成"听得见的句子",
# 这是"它识别出一些别的"的来源之一。现在: 太轻直接不送模型, 增益封顶 30 倍 (约 +30dB),
# 小声说话仍然放得出来, 剩下的幻觉交给重复过滤和置信度闸门兜。
_MIN_PEAK = 0.0035          # 约 -49dBFS, 再低就不值得浪费一次推理
_MAX_GAIN = 30.0

# 解码防幻觉: 短句贪心解码 + 无温度回退时, whisper 会在近静音上卡进
# "好、好、好、好…" 的重复循环。开温度回退 + 重复惩罚, 让它重解码而不是硬吐。
_BEAM = max(1, min(5, int(os.environ.get("JARVIS_WHISPER_BEAM", "3") or 3)))
_SEP_CHARS = "、,，。.！!？?…;；:：'\"“”‘’()（）[]【】 \n\t"

SUPPORTED_SIZES = ("base", "small", "medium")
_MODEL = None
_MODEL_SIZE = None
_LOAD_LOCK = threading.Lock()

# faster-whisper / ctranslate2 的模型不是线程安全的：listen_continuous 的后台线程
# 和 confirm() 里的 recognize_once 会同时打同一个 _MODEL，并发进 native 层会段错误
# （进程静默消失、没有 traceback）。所有推理串行化，排队等待远比崩溃划算。
_TRANSCRIBE_LOCK = threading.Lock()

# 心跳：让外部能区分"真的在监听"和"线程还活着但再也读不到音频"
_HEARTBEAT_LOCK = threading.Lock()
_HEARTBEAT = {
    "state": "idle",      # idle / listening / read_error / stopped / fatal
    "last_read": 0.0,     # 最后一次成功读到音频块的时间
    "read_fails": 0,      # 当前连续失败次数
    "restarts": 0,        # 输入流重建次数
    "last_error": "",
}


def last_heartbeat() -> dict:
    """当前麦克风监听的实时状态快照 (供 UI / 看门狗判断是否真的在听)。"""
    with _HEARTBEAT_LOCK:
        return dict(_HEARTBEAT)


def _beat(**kwargs):
    with _HEARTBEAT_LOCK:
        _HEARTBEAT.update(kwargs)

# whisper 在纯噪音/呼吸声上容易编出来的句子, 直接丢掉
_HALLUCINATION = (
    "字幕", "点赞", "订阅", "转发", "打赏", "谢谢观看", "感谢观看",
    "明镜", "点点栏目", "请不吝", "字幕由", "amara", "thanks for watching",
    "thank you for watching", "subtitles by", "please subscribe",
    "以下是普通话",
)

# 单说一个词时模型爱蹦出来的语气词, 不是指令
_NOISE_EN = {"you", "oh", "uh", "um", "hmm", "the", "and", "it", "is", "a", "i"}


def model_dir(size: str = "small") -> Path:
    return BASE / "models" / f"faster-whisper-{size}"


def available(size: str = "small") -> bool:
    return (model_dir(size) / "model.bin").exists()


def ensure_model(size: str = "small", log=print) -> bool:
    """模型不在就自动从国内镜像拉一份 (只在下模型时需要联网)"""
    d = model_dir(size)
    if (d / "model.bin").exists():
        return True
    if size not in SUPPORTED_SIZES:
        log(f"[语音] 不认识的模型规格: {size}")
        return False
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from huggingface_hub import snapshot_download
        log(f"[语音] 首次使用, 正在下载 whisper-{size} 模型 (约 461MB)…")
        snapshot_download(
            repo_id=f"Systran/faster-whisper-{size}",
            local_dir=str(d),
            allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        )
        return (d / "model.bin").exists()
    except Exception as e:
        log(f"[语音] 模型下载失败: {type(e).__name__}: {e}")
        log("[语音] 可以手动把 Systran/faster-whisper-small 的文件放进 " + str(d))
        return False


def _load(size: str = "small"):
    global _MODEL, _MODEL_SIZE
    with _LOAD_LOCK:
        if _MODEL is not None and _MODEL_SIZE == size:
            return _MODEL
        from faster_whisper import WhisperModel
        d = model_dir(size)
        t0 = time.time()
        # 24 核机器不再一次开 12 个线程，降低启动/单句推理的瞬时峰值；
        # 需要更快识别时可用 JARVIS_WHISPER_THREADS 覆盖。
        threads = int(os.environ.get("JARVIS_WHISPER_THREADS", "0") or 0)
        if threads <= 0:
            threads = min(8, max(4, (os.cpu_count() or 4) // 3))
        _MODEL = WhisperModel(
            str(d), device="cpu", compute_type="int8",
            cpu_threads=threads,
            local_files_only=True,
        )
        _MODEL_SIZE = size
        print(f"[语音] whisper-{size} 已加载 ({time.time() - t0:.1f}s)")
        return _MODEL


# 常见简短有效口语/打招呼词白名单，即便字母少于4个也不过滤
_ALLOWED_SHORT_EN = {"hi", "hey", "ok", "yes", "no", "bye", "jarvis"}


def _clean(text: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    for bad in _HALLUCINATION:
        if bad in low:
            return ""
    # 重复解码幻觉: 黑名单挡不住的"好、好、好…" / 单字刷屏
    if _repetitive(t):
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in t):
        # 没有汉字时检查是否在短词白名单中
        pure_en = low.strip(" .?!,")
        if pure_en in _ALLOWED_SHORT_EN:
            return t.strip(" 。，,.！!？?、\n\t")
        # 否则要求是像样的英文, 过滤 "you" "oh" 这类噪音碎片
        letters = sum(ch.isalpha() for ch in t)
        if letters < 3 or len(t.split()) < 1 or pure_en in _NOISE_EN:
            return ""
    return t.strip(" 。，,.！!？?、\n\t")


def _repetitive(text: str) -> bool:
    """重复解码幻觉检测: "好、好、好、好…" 或单字刷屏。

    whisper 在近静音上会卡进重复循环, 夹着顿号/逗号时固定黑名单完全挡不住;
    重复片段连续出现 4 次以上就整句丢弃 (两连重复如"好的好的"仍然放行)。
    """
    s = "".join(ch for ch in text if ch not in _SEP_CHARS)
    if not s:
        return True
    if len(s) >= 6 and s.count(s[0]) >= len(s) - 1:
        return True
    for n in (1, 2, 3, 4):
        for i in range(len(s) - n * 4 + 1):
            unit = s[i:i + n]
            reps, j = 1, i + n
            while s[j:j + n] == unit:
                reps += 1
                j += n
                if reps >= 4:
                    return True
    return False


def _normalize(arr):
    """麦克风阵列出来的声音常常很小, 拉到正常音量再交给模型 (准确率明显更好)。

    返回 None 表示这段基本是底噪: 不送模型, 也不把噪声放大成"听得见的句子"。
    """
    import numpy as np
    if not arr.size:
        return None
    arr = arr - float(arr.mean())
    peak = float(np.max(np.abs(arr)))
    if peak < _MIN_PEAK:
        return None
    gain = 0.9 / peak
    if gain < 1.05:
        return arr
    return arr * min(gain, _MAX_GAIN)


def transcribe_detail(audio, size: str = "small", language="zh") -> dict:
    """识别并带回置信度: {"text", "logprob", "no_speech", "reason"}。

    logprob 是保留下来的片段平均 avg_logprob, 上层用它决定"这句要不要执行":
    低于门槛就只提示没听清, 不许拿不确定的识别结果去动电脑。
    """
    import numpy as np
    if isinstance(audio, (bytes, bytearray)):
        arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        arr = np.asarray(audio, dtype=np.float32)
    if arr.size < SAMPLE_RATE // 4:      # 短于 0.25s 不识别
        return {"text": "", "logprob": 0.0, "no_speech": 1.0, "reason": "太短"}
    arr = _normalize(arr)
    if arr is None:
        return {"text": "", "logprob": 0.0, "no_speech": 1.0, "reason": "底噪"}
    model = _load(size)
    lp_sum, lp_n, ns_max = 0.0, 0, 0.0
    keep = []
    # 串行化推理：ctranslate2 的 segment 是惰性生成器，真正的计算发生在迭代时，
    # 因此锁必须包住整个 transcribe + 迭代过程，而不是只包住调用。
    with _TRANSCRIBE_LOCK:
        segs, _info = model.transcribe(
            arr,
            language=language,               # 默认 zh; 只有显式传 None 才自动判断中/英
            beam_size=_BEAM,
            # 温度回退: 重复/低质量结果会按更高温度重解码, 而不是硬吐一串"好好好"
            temperature=[0.0, 0.2, 0.4, 0.6],
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.6,
            no_speech_threshold=0.78,
            no_repeat_ngram_size=3,
            repetition_penalty=1.1,
            vad_filter=False,                # 上层已经切好句子, 不用再跑一遍 VAD
            condition_on_previous_text=False,
            without_timestamps=True,
            # 提供中英双语环境上下文: 避免英文短词被强制当成中文杂音丢掉，同时保持普通话优先级
            initial_prompt="以下是普通话和常用英文指令（如 Hello, Hi, Jarvis, OK）的对话。",
        )
        # whisper 在噪音上会一本正经地编句子, 用模型自带的置信度把这类结果挡掉
        for s in segs:
            ns = float(getattr(s, "no_speech_prob", 0.0) or 0.0)
            lp = float(getattr(s, "avg_logprob", 0.0) or 0.0)
            ns_max = max(ns_max, ns)
            if ns > 0.78:
                continue
            if lp < -1.6:
                continue
            keep.append(s.text)
            lp_sum += lp
            lp_n += 1
    text = _clean("".join(keep))
    return {
        "text": text,
        "logprob": (lp_sum / lp_n) if lp_n else -2.0,
        "no_speech": ns_max,
        "reason": "" if text else "无有效片段",
    }


def transcribe(audio, size: str = "small", language="zh") -> str:
    """audio: float32 numpy (16k 单声道) 或 int16 字节; 返回识别文本"""
    return transcribe_detail(audio, size=size, language=language)["text"]


class _Segmenter:
    """能量 VAD: 把连续音频流切成一句一句"""

    def __init__(self):
        # 不能从 None 起步: 第一帧如果是小声说话, 会把 floor 直接设成说话电平,
        # 门限反而永远压在自己的声音之上。给一个很低的初始基底, 让启动自检去校准。
        self.floor = 5.0
        self.buf = []
        self.rolling = []
        self.speaking = False
        self.silence = 0
        self.voiced = 0

    def _threshold(self):
        # 下限压到 65: 本机麦克风安静时峰值只有 39~99, 旧下限 150 会把小声说话整句漏掉。
        # 用 floor*1.5+15 跟随环境噪声, 而不是死翻倍, 避免环境稍吵时门限被抬得过高。
        f = self.floor if self.floor and self.floor > 1 else 10.0
        return max(65.0, min(2500.0, f * 1.5 + 15.0))

    def push(self, chunk: bytes):
        """喂 30ms 音频; 返回一句话的 int16 字节 (没有就是 None)"""
        import audioop
        try:
            rms = float(audioop.rms(chunk, 2))
        except Exception:
            rms = 0.0
        # 噪声基底跟着环境慢慢走 (只往下跟得快, 往上跟得慢)
        if self.floor is None:
            self.floor = rms
        elif rms < self.floor:
            self.floor = self.floor * 0.7 + rms * 0.3
        else:
            self.floor = self.floor * 0.995 + rms * 0.005

        self.rolling.append(chunk)
        if len(self.rolling) > _PRE_ROLL:
            self.rolling.pop(0)

        voiced = rms > self._threshold()
        if not self.speaking:
            if voiced:
                # 声音足够大就立刻开始, 否则要求连续两声, 避免咳嗽/敲键盘误触发
                self.voiced += 1
                if self.voiced >= 2 or rms > self._threshold() * 2.5:
                    self.speaking = True
                    self.silence = 0
                    self.buf = list(self.rolling)
            else:
                self.voiced = 0
            return None

        self.buf.append(chunk)
        if voiced:
            self.silence = 0
        else:
            self.silence += 1

        if self.silence >= _SIL_END or len(self.buf) >= _MAX_UTTER:
            data = b"".join(self.buf)
            speech_chunks = len(self.buf) - self.silence
            self.speaking = False
            self.voiced = 0
            self.silence = 0
            self.buf = []
            self.rolling = []
            if speech_chunks < _MIN_SPEECH:
                return None
            return data
        return None


def listen_continuous(on_text, stop_event=None, hotwords=None, ignore_while=None,
                      on_ready=None, size="small", on_level=None, language="zh",
                      on_error=None, send_meta=False):
    """后台线程持续听; 每识别出一句调用 on_text(text)

    send_meta=True 时 on_text 收到 (text, meta) 二元组, meta 带 logprob/no_speech,
    上层据此决定"这句要不要执行"(低置信度只提示没听清, 不动电脑)。
    ignore_while: 返回 True 时丢弃这句 (跳过贾维斯自己说话的回声)
    on_ready:     真正开始监听后回调一次
    on_error:     输入流无法恢复时回调一次 (上层应把界面从 LISTENING 切走)
    """
    import pyaudio

    if not available(size):
        raise RuntimeError(f"whisper 模型不存在: {model_dir(size)}")

    _load(size)   # 预热, 避免第一句话等 3 秒

    def _open_stream():
        # 借用进程级 PortAudio 单例。绝不 terminate：
        # 那会把同一进程里 TTS 输出流和其它输入流一起拆掉。
        return audio_device.open_input(
            format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
            input=True, frames_per_buffer=CHUNK,
        )

    stream = _open_stream()
    seg = _Segmenter()
    # 开机自检: 报一下麦克风实际电平, 方便判断"听不见"是设备问题还是嘴离太远
    try:
        import audioop
        probe = [audioop.rms(stream.read(CHUNK, exception_on_overflow=False), 2) for _ in range(10)]
        avg = sum(probe) / max(1, len(probe))
        # 即使启动时几乎无声, 也要给一个低基底, 否则第一句小声说话会把 floor
        # 直接抬成语音电平, 反而让门限永远高于自己的说话声。
        seg.floor = max(5.0, avg)
        print(f"[语音] 麦克风电平: 平均 {int(avg)} 峰值 {max(probe)} "
              f"(安静环境正常值 <200), 当前门限 {seg._threshold():.0f}")
    except Exception as e:
        print(f"[语音] 电平自检失败, 继续监听: {type(e).__name__}: {e}")
    print("[语音] whisper 持续识别已启动")
    _beat(state="listening", last_read=time.time(), read_fails=0, last_error="")
    if on_ready is not None:
        on_ready()

    ignore_current = False
    read_fails = 0
    restarts = 0
    beat_tick = 0
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                chunk = stream.read(CHUNK, exception_on_overflow=False)
                if not chunk:
                    # 空块说明设备已经不给数据了，必须当故障处理，不能继续空转
                    raise IOError("麦克风返回空音频块")
            except Exception as e:
                read_fails += 1
                _beat(state="read_error", read_fails=read_fails,
                      last_error=f"{type(e).__name__}: {e}")
                if read_fails < _MAX_READ_FAILS:
                    # 短时抖动（缓冲溢出等）先退让一下，避免把 CPU 空转成 100%
                    time.sleep(0.05)
                    continue
                # 连续读失败 = 输入流已经坏了。
                # 旧代码在这里只是 continue 空转：界面显示"聆听中"、实际永远听不到，
                # 这正是"卡死"的真身。现在改成重建流自愈。
                print(f"[语音] 麦克风连续读取失败 {read_fails} 次 "
                      f"({type(e).__name__}: {e})，正在重建输入流…")
                audio_device.release_stream(stream)
                stream = None
                time.sleep(0.3)
                try:
                    stream = _open_stream()
                except Exception as re_err:
                    msg = f"麦克风无法恢复: {type(re_err).__name__}: {re_err}"
                    print(f"[语音] {msg}")
                    _beat(state="fatal", last_error=msg)
                    if on_error is not None:
                        try:
                            on_error(msg)
                        except Exception:
                            pass
                    raise RuntimeError(msg)
                seg = _Segmenter()
                read_fails = 0
                restarts += 1
                _beat(state="listening", read_fails=0, restarts=restarts,
                      last_read=time.time(), last_error="")
                print(f"[语音] 输入流已重建（第 {restarts} 次），继续监听")
                continue

            if read_fails:
                # 从故障里恢复：立刻刷新心跳，让看门狗知道音频又开始流动了
                read_fails = 0
                _beat(state="listening", read_fails=0, last_error="")
            beat_tick += 1
            if beat_tick >= 10:          # 约 300ms 一次，不必每块都抢锁
                beat_tick = 0
                _beat(last_read=time.time())
            if on_level is not None:
                try:
                    on_level(chunk)
                except Exception:
                    pass
            # 在“这句话刚开始”时判断是否处于贾维斯说话中。
            # 不能等到整句结束再判断：用户如果在句间 0.9 秒留白里开口，
            # 下一句 TTS 可能在他说完前又开始，末端判断会把这句整句误丢。
            was_speaking = seg.speaking
            data = seg.push(chunk)
            if not was_speaking and seg.speaking:
                ignore_current = bool(ignore_while is not None and ignore_while())
            if not data:
                continue
            if ignore_current:
                ignore_current = False
                continue
            ignore_current = False
            try:
                info = transcribe_detail(data, size=size, language=language)
            except Exception as e:
                print(f"[语音] 识别失败: {type(e).__name__}: {e}")
                continue
            text = info.get("text") or ""
            if text:
                on_text((text, info) if send_meta else text)
            elif len(data) / 2 / SAMPLE_RATE >= 0.5:
                # 只记录“确实听到一段声音但没识别出字”的情况, 方便区分麦克风/VAD/模型三类问题
                print(f"[语音] 切出一段 {len(data) / 2 / SAMPLE_RATE:.1f}s 音频, "
                      f"但没识别出文字 ({info.get('reason') or '-'})")
    finally:
        _beat(state="stopped")
        audio_device.release_stream(stream)


def recognize_once(timeout: float = 8.0, hotwords=None, size="small") -> str:
    """听一句返回文本; 超时或没听到返回空串 (阻塞, 别放主线程)"""
    import pyaudio

    if not available(size):
        return ""
    _load(size)
    try:
        stream = audio_device.open_input(
            format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
            input=True, frames_per_buffer=CHUNK,
        )
    except Exception as e:
        print(f"[语音] 打开麦克风失败: {type(e).__name__}: {e}")
        return ""
    seg = _Segmenter()
    end = time.time() + timeout
    fails = 0
    try:
        while time.time() < end:
            try:
                chunk = stream.read(CHUNK, exception_on_overflow=False)
                if not chunk:
                    raise IOError("麦克风返回空音频块")
            except Exception as e:
                fails += 1
                if fails >= _MAX_READ_FAILS:
                    print(f"[语音] 单次识别读取失败过多, 放弃: {type(e).__name__}: {e}")
                    return ""
                time.sleep(0.05)
                continue
            fails = 0
            data = seg.push(chunk)
            if not data:
                continue
            try:
                text = transcribe(data, size=size)
            except Exception:
                return ""
            if text:
                return text
        return ""
    finally:
        audio_device.release_stream(stream)
