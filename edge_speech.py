# -*- coding: utf-8 -*-
"""
在线高自然度 TTS 引擎 (Edge-TTS)
包含微软云端超逼真神经网络音色 (比如云希、云扬、云健、晓晓等)
"""
import asyncio
import os
import tempfile
import wave
from pathlib import Path

import audio_device

# 常用好听的角色音色映射
PRESET_VOICES = {
    "云希": "zh-CN-YunxiNeural",       # 磁性沉稳男声（最像高科技助手/男主角）
    "云扬": "zh-CN-YunyangNeural",     # 专业男播音员（稳重、新闻质感）
    "云健": "zh-CN-YunjianNeural",     # 温暖写实男声
    "晓晓": "zh-CN-XiaoxiaoNeural",    # 经典高保真自然女声
    "晓伊": "zh-CN-XiaoyiNeural",      # 活泼甜美女声
}

async def _synthesize(text: str, voice: str, output_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


async def _synthesize_with_retry(text: str, voice: str, output_path: str, attempts: int = 2):
    """Edge 云端偶尔返回空音频; 重试一次, 减少突然降级成本地嗓音。"""
    last_error = None
    for i in range(max(1, attempts)):
        try:
            try:
                os.unlink(output_path)
            except OSError:
                pass
            await _synthesize(text, voice, output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1200:
                return
            raise RuntimeError("Edge TTS 返回空音频")
        except Exception as e:
            last_error = e
            print(f"[Edge TTS] 第 {i + 1} 次合成失败: {type(e).__name__}: {str(e)[:160]}")
            if i + 1 < attempts:
                await asyncio.sleep(0.35)
    raise last_error or RuntimeError("Edge TTS 合成失败")

def speak_edge(text: str, voice_name: str = "zh-CN-YunxiNeural", on_start=None):
    """使用 Edge TTS 合成逼真人类音色并播放。

    on_start: 合成完成、真正开始播放音频前调用；用于让字幕和语音同时出现，
    而不是在云端合成（可能 1~2 秒）之前就提前显示字幕。
    """
    # 允许直接传别名，比如 "云希"
    voice = PRESET_VOICES.get(voice_name, voice_name)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(tmp_fd)
    try:
        asyncio.run(_synthesize_with_retry(text, voice, tmp_path))
        if on_start is not None:
            try:
                on_start()
            except Exception:
                pass
        from custom_voice import play_audio_file
        play_audio_file(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─────────────── 机器人音效版本 (纯 pygame + numpy, 不依赖 ffmpeg) ───────────────

ROBOT_PITCH_FACTOR = 0.93   # 降调系数, 越小越低沉


def _resample_int16(samples, factor: float):
    """线性重采样: factor < 1 → 变慢变低沉"""
    import numpy as np
    if abs(factor - 1.0) < 1e-6 or len(samples) == 0:
        return samples
    new_len = max(1, int(round(len(samples) / factor)))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16)


def speak_edge_robot(text: str, voice_name: str = "zh-CN-YunxiNeural",
                     intensity: float = 0.7, pitch_factor: float = ROBOT_PITCH_FACTOR,
                     on_start=None):
    """Edge TTS 合成 → 转采样数组 → 降调 + 机器人音效 → 播放"""
    voice = PRESET_VOICES.get(voice_name, voice_name)
    mp3_fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
    os.close(mp3_fd)
    wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav_fd)
    try:
        asyncio.run(_synthesize_with_retry(text, voice, mp3_path))

        import numpy as np
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=24000, size=-16, channels=1)
        freq, size, channels = pygame.mixer.get_init()
        if size != -16:
            raise RuntimeError(f"不支持的混音格式: {pygame.mixer.get_init()}")

        snd = pygame.mixer.Sound(mp3_path)
        arr = pygame.sndarray.samples(snd).copy()
        if channels == 2:
            mono = ((arr[:, 0].astype(np.int32) + arr[:, 1].astype(np.int32)) // 2).astype(np.int16)
        else:
            mono = arr.reshape(-1)

        from robot_voice import apply_robot_effect
        processed = apply_robot_effect(mono, freq, intensity)
        processed = _resample_int16(processed, pitch_factor)

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(freq)
            wf.writeframes(processed.tobytes())

        if on_start is not None:
            try:
                on_start()
            except Exception:
                pass
        from custom_voice import play_audio_file
        play_audio_file(wav_path)
    finally:
        for p in (mp3_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ─────────────── 整段流式 Edge TTS (连续音频 + 句边界字幕) ───────────────

class _RobotStream:
    """把机器人音效做成有状态的流处理器，避免每块 PCM 重新起相位。"""

    def __init__(self, sample_rate: int, intensity: float = 0.7):
        import numpy as np

        self.rate = int(sample_rate)
        self.intensity = max(0.0, min(1.0, float(intensity)))
        self.hist_len = max(
            int(0.012 * self.rate),
            int(0.025 * self.rate),
            int(0.055 * self.rate),
        )
        self.history = np.zeros(self.hist_len, dtype=np.float64)
        self.n = 0

    def process(self, pcm: bytes) -> bytes:
        if not pcm or self.intensity <= 0:
            return pcm
        import numpy as np

        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        if x.size == 0:
            return pcm

        n = x.size
        t = (self.n + np.arange(n, dtype=np.float64)) / self.rate
        base = x * (0.6 + 0.4 * np.sin(2.0 * np.pi * 42.0 * t))
        full = np.concatenate([self.history, base])
        effects = base.copy()
        for delay_sec, gain in ((0.012, 0.25), (0.025, 0.20), (0.055, 0.10)):
            d = int(delay_sec * self.rate)
            start = self.hist_len - d
            end = start + n
            if start >= 0 and end <= full.size:
                effects += gain * full[start:end]

        mixed = x * (1.0 - self.intensity) + effects * self.intensity
        mixed = np.clip(mixed, -32768.0, 32767.0)
        self.history = full[-self.hist_len:]
        self.n += n
        return mixed.astype(np.int16).tobytes()


def speak_edge_streaming(text: str, voice_name: str = "zh-CN-YunxiNeural",
                         on_start=None, on_sentence=None, robot_intensity: float = 0.0,
                         stop_event=None, on_level=None):
    """整段流式合成并播放：音频边下边播，字幕按 Edge 句边界时间戳切换。

    - 不再按句反复起停，句间没有 0.9 秒的人为留白；
    - 首块音频到达就开始播放，不必等整段云端合成完；
    - on_sentence(text) 在每句音频真正开始时回调；
    - stop_event 可用于 Esc 打断。
    """
    text = str(text or "").strip()
    if not text:
        return
    voice = PRESET_VOICES.get(voice_name, voice_name)
    try:
        robot_intensity = float(robot_intensity or 0.0)
    except (TypeError, ValueError):
        robot_intensity = 0.0
    robot_intensity = max(0.0, min(1.0, robot_intensity))

    import queue
    import threading
    import time

    import av
    import numpy as np
    import pyaudio

    rate = 24000
    audio_q = queue.Queue(maxsize=128)
    event_q = queue.Queue()
    done = threading.Event()
    stop_flag = threading.Event()
    errors = []

    def stopped():
        if stop_flag.is_set():
            return True
        return stop_event is not None and stop_event.is_set()

    def safe_cb(cb, *args):
        if cb is None:
            return
        try:
            cb(*args)
        except Exception:
            pass

    def producer():
        try:
            import edge_tts

            codec = av.CodecContext.create("mp3", "r")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

            async def consume():
                communicate = edge_tts.Communicate(text, voice)
                async for chunk in communicate.stream():
                    if stopped():
                        return
                    typ = chunk.get("type")
                    if typ == "audio":
                        for packet in codec.parse(chunk["data"]):
                            for frame in codec.decode(packet):
                                out_frames = resampler.resample(frame)
                                if not isinstance(out_frames, (list, tuple)):
                                    out_frames = [out_frames]
                                for rf in out_frames:
                                    arr = rf.to_ndarray().reshape(-1)
                                    pcm = arr.astype(np.int16).tobytes()
                                    while not stopped():
                                        try:
                                            audio_q.put(pcm, timeout=0.05)
                                            break
                                        except queue.Full:
                                            continue
                    elif typ == "SentenceBoundary":
                        try:
                            event_q.put({
                                "offset": float(chunk.get("offset", 0)) / 10_000_000.0,
                                "duration": float(chunk.get("duration", 0)) / 10_000_000.0,
                                "text": str(chunk.get("text", "") or "").strip(),
                            })
                        except Exception:
                            pass

            asyncio.run(consume())
        except Exception as e:
            errors.append(e)
        finally:
            done.set()
            while True:
                try:
                    audio_q.put(None, timeout=0.05)
                    break
                except queue.Full:
                    if stopped():
                        break

    producer_thread = threading.Thread(target=producer, daemon=True)

    # PortAudio 是进程级全局资源：这里只能借用共享实例，绝不能 terminate，
    # 否则会把 whisper 常驻输入流一起拆掉（"说完一句话就再也听不见"的根因）
    out = audio_device.open_output(format=pyaudio.paInt16, channels=1, rate=rate,
                                   output=True, frames_per_buffer=1024)
    producer_thread.start()
    started = False
    t0 = None
    last_level_at = 0.0
    events = []
    robot = _RobotStream(rate, robot_intensity) if robot_intensity and robot_intensity > 0 else None

    def drain_events():
        while True:
            try:
                events.append(event_q.get_nowait())
            except queue.Empty:
                break

    def display_due(force=False):
        if t0 is None:
            return
        elapsed = time.monotonic() - t0
        for ev in events:
            if ev.get("shown"):
                continue
            # 略微提前 0.12 秒切换，抵掉 UI 桥接与页面重绘延迟；
            # 宁可字幕早一帧，也不要让用户先听到下一句、才看到字幕。
            if force or (ev["text"] and ev["offset"] <= elapsed + 0.12):
                ev["shown"] = True
                safe_cb(on_sentence, ev["text"])

    try:
        while True:
            if stopped():
                break
            try:
                item = audio_q.get(timeout=0.1)
            except queue.Empty:
                drain_events()
                if done.is_set() and audio_q.empty():
                    break
                continue
            if item is None:
                break

            if not started:
                started = True
                t0 = time.monotonic()
                safe_cb(on_start)
            if robot is not None:
                item = robot.process(item)
            out.write(item)
            if on_level is not None:
                now = time.monotonic()
                if now - last_level_at >= 0.05:
                    last_level_at = now
                    try:
                        # 与 custom_voice._emit_audio_level 的约定一致: 传 16 位 PCM 字节
                        on_level(item)
                    except Exception:
                        pass
            drain_events()
            display_due()

        drain_events()
        display_due(force=True)
        if not started:
            if errors:
                raise errors[0]
            raise RuntimeError("Edge 流式合成没有返回音频")
        if errors:
            print(f"[Edge 流式 TTS] 中途结束: {type(errors[0]).__name__}: {str(errors[0])[:160]}")
        # 给硬件缓冲一点时间播完最后一帧
        time.sleep(0.15)
    finally:
        stop_flag.set()
        # 只关自己这一条输出流；全局 PortAudio 在进程退出时由 audio_device 统一释放
        try:
            audio_device.release_stream(out)
        except Exception:
            pass
        try:
            producer_thread.join(timeout=0.5)
        except Exception:
            pass

