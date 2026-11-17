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

BASE = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
CHUNK = 480                 # 30ms
_PRE_ROLL = 10              # 保留 300ms 前置音频, 避免吃掉字头
_SIL_END = 18               # 连续 540ms 静音视为说完
_MIN_SPEECH = 15            # 至少 450ms 有效语音才算一句
_MAX_UTTER = 400            # 单句最长 12s, 到点强制切

SUPPORTED_SIZES = ("base", "small", "medium")
_MODEL = None
_MODEL_SIZE = None
_LOAD_LOCK = threading.Lock()

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


def _clean(text: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    for bad in _HALLUCINATION:
        if bad in low:
            return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in t):
        # 没有汉字时要求是像样的英文, 过滤 "you" "oh" 这类噪音碎片
        letters = sum(ch.isalpha() for ch in t)
        if letters < 4 or len(t.split()) < 1 or low.strip(".") in _NOISE_EN:
            return ""
    return t.strip(" 。，,.！!？?、\n\t")


def _normalize(arr):
    """麦克风阵列出来的声音常常很小, 拉到正常音量再交给模型 (准确率明显更好)"""
    import numpy as np
    arr = arr - float(arr.mean())
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if 1e-4 < peak < 0.35:
        arr = arr * (0.9 / peak)
    return arr


def transcribe(audio, size: str = "small", language=None) -> str:
    """audio: float32 numpy (16k 单声道) 或 int16 字节; 返回识别文本"""
    import numpy as np
    model = _load(size)
    if isinstance(audio, (bytes, bytearray)):
        arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        arr = np.asarray(audio, dtype=np.float32)
    if arr.size < SAMPLE_RATE // 4:      # 短于 0.25s 不识别
        return ""
    arr = _normalize(arr)
    segs, _info = model.transcribe(
        arr,
        language=language,               # None = 自动判断中/英
        beam_size=1,
        vad_filter=False,                # 上层已经切好句子, 不用再跑一遍 VAD
        condition_on_previous_text=False,
        without_timestamps=True,
        # 给点中文语境: 否则模型偶尔会输出繁体字, 英文也不会因此受影响
        initial_prompt="以下是普通话的句子。",
    )
    # whisper 在噪音上会一本正经地编句子, 用模型自带的置信度把这类结果挡掉
    keep = []
    for s in segs:
        if getattr(s, "no_speech_prob", 0.0) > 0.55:
            continue
        if getattr(s, "avg_logprob", 0.0) < -1.0:
            continue
        keep.append(s.text)
    return _clean("".join(keep))


class _Segmenter:
    """能量 VAD: 把连续音频流切成一句一句"""

    def __init__(self):
        self.floor = None
        self.buf = []
        self.rolling = []
        self.speaking = False
        self.silence = 0
        self.voiced = 0

    def _threshold(self):
        # 下限压到 150: 有些机器的麦克风阵列本身很"安静", 阈值太高会完全听不到
        f = self.floor if self.floor and self.floor > 1 else 80.0
        return max(150.0, min(4000.0, f * 2.0))

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
                      on_ready=None, size="small", on_level=None):
    """后台线程持续听; 每识别出一句调用 on_text(text)

    ignore_while: 返回 True 时丢弃这句 (跳过贾维斯自己说话的回声)
    on_ready:     真正开始监听后回调一次
    """
    import pyaudio

    if not available(size):
        raise RuntimeError(f"whisper 模型不存在: {model_dir(size)}")

    _load(size)   # 预热, 避免第一句话等 3 秒

    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                     input=True, frames_per_buffer=CHUNK)
    seg = _Segmenter()
    # 开机自检: 报一下麦克风实际电平, 方便判断"听不见"是设备问题还是嘴离太远
    try:
        import audioop
        probe = [audioop.rms(stream.read(CHUNK, exception_on_overflow=False), 2) for _ in range(10)]
        print(f"[语音] 麦克风电平: 平均 {sum(probe)//10} 峰值 {max(probe)} (安静环境正常值 <200)")
    except Exception:
        pass
    print("[语音] whisper 持续识别已启动")
    if on_ready is not None:
        on_ready()
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                chunk = stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                continue
            if on_level is not None:
                try:
                    on_level(chunk)
                except Exception:
                    pass
            data = seg.push(chunk)
            if not data:
                continue
            if ignore_while is not None and ignore_while():
                continue
            try:
                text = transcribe(data, size=size)
            except Exception as e:
                print(f"[语音] 识别失败: {type(e).__name__}: {e}")
                continue
            if text:
                on_text(text)
    finally:
        try:
            stream.stop_stream()
            stream.close()
            pa.terminate()
        except Exception:
            pass


def recognize_once(timeout: float = 8.0, hotwords=None, size="small") -> str:
    """听一句返回文本; 超时或没听到返回空串 (阻塞, 别放主线程)"""
    import pyaudio

    if not available(size):
        return ""
    _load(size)
    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                     input=True, frames_per_buffer=CHUNK)
    seg = _Segmenter()
    end = time.time() + timeout
    try:
        while time.time() < end:
            try:
                chunk = stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                continue
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
        try:
            stream.stop_stream()
            stream.close()
            pa.terminate()
        except Exception:
            pass
