# -*- coding: utf-8 -*-
"""
贾维斯机器人音效处理
把普通 Windows TTS 语音变成科幻风金属质感嗓音

原理:
  1. 环形调制 (Ring Modulation) — 用低频正弦波乘以语音信号, 产生金属 / 电子质感
  2. 合唱叠加 (Chorus) — 叠加一份微偏移副本, 声音更厚实
  3. 双短回声 (Dual Echo) — 25ms + 55ms 两层短延迟, 模拟通讯系统空间感
  4. 干 / 湿混合 — 通过 intensity 参数控制效果深浅
"""
import os
import tempfile
import wave

import numpy as np


# ═══════════════ 核心: 音频效果 ═══════════════

def apply_robot_effect(samples: np.ndarray, sample_rate: int,
                       intensity: float = 0.7) -> np.ndarray:
    """
    对 int16 音频采样施加机器人音效

    Parameters
    ----------
    samples : np.ndarray (int16)
    sample_rate : int
    intensity : float  0.0 = 原声 → 1.0 = 全效果

    Returns
    -------
    np.ndarray (int16)
    """
    intensity = max(0.0, min(1.0, intensity))
    if intensity == 0:
        return samples

    dry = samples.astype(np.float64)
    wet = dry.copy()
    n = len(wet)
    t = np.arange(n, dtype=np.float64) / sample_rate

    # ── 1. 环形调制: 42 Hz 正弦波 ──
    ring_freq = 42
    ring = np.sin(2.0 * np.pi * ring_freq * t)
    wet = wet * (0.6 + 0.4 * ring)

    # ── 2. 合唱: 12ms 偏移副本, 混入 25% ──
    chorus_d = int(0.012 * sample_rate)
    if chorus_d < n:
        chorus = np.zeros(n, dtype=np.float64)
        chorus[chorus_d:] = wet[: n - chorus_d] * 0.25
        wet += chorus

    # ── 3. 双短回声: 25ms (20%) + 55ms (10%) ──
    for delay_sec, gain in [(0.025, 0.20), (0.055, 0.10)]:
        d = int(delay_sec * sample_rate)
        if d < n:
            echo = np.zeros(n, dtype=np.float64)
            echo[d:] = wet[: n - d] * gain
            wet += echo

    # ── 4. 干 / 湿混合 ──
    mixed = dry * (1.0 - intensity) + wet * intensity

    # 归一化, 防止削波
    peak = np.max(np.abs(mixed))
    if peak > 1:
        mixed = mixed / peak * 30000.0

    return mixed.astype(np.int16)


def process_wav_file(src_path: str, dst_path: str, intensity: float = 0.7):
    """读取 WAV → 施加机器人音效 → 写出新 WAV"""
    with wave.open(src_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if not raw:
        raise RuntimeError("音频为空")
    if sample_width != 2:
        raise RuntimeError(f"不支持的采样宽度: {sample_width} 字节")

    samples = np.frombuffer(raw, dtype=np.int16)
    if n_channels == 2:
        mono = ((samples[0::2].astype(np.float64) + samples[1::2].astype(np.float64)) / 2.0).astype(np.int16)
        processed = apply_robot_effect(mono, framerate, intensity)
        stereo = np.empty(len(processed) * 2, dtype=np.int16)
        stereo[0::2] = processed
        stereo[1::2] = processed
        output = stereo.tobytes()
    else:
        processed = apply_robot_effect(samples, framerate, intensity)
        output = processed.tobytes()

    with wave.open(dst_path, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(framerate)
        wf.writeframes(output)


# ═══════════════ 高层接口 ═══════════════

def speak_robot(engine, text: str, intensity: float = 0.7):
    """
    用 pyttsx3 引擎生成语音 → 施加机器人音效 → 播放

    Parameters
    ----------
    engine : pyttsx3.Engine
    text : str
    intensity : float  效果强度 0.0 ~ 1.0
    """
    import pyaudio

    # 保存到临时 WAV
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    try:
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()

        # 读取 WAV
        with wave.open(tmp_path, "rb") as wf:
            n_channels  = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate    = wf.getframerate()
            raw          = wf.readframes(wf.getnframes())

        if not raw:
            raise RuntimeError("TTS 生成了空音频")
        if sample_width != 2:
            raise RuntimeError(f"不支持的采样宽度: {sample_width} 字节")

        samples = np.frombuffer(raw, dtype=np.int16)

        # 立体声 → 先转单声道处理, 再还原
        if n_channels == 2:
            left  = samples[0::2].astype(np.float64)
            right = samples[1::2].astype(np.float64)
            mono  = ((left + right) / 2.0).astype(np.int16)
            processed = apply_robot_effect(mono, framerate, intensity)
            stereo = np.empty(len(processed) * 2, dtype=np.int16)
            stereo[0::2] = processed
            stereo[1::2] = processed
            output_bytes = stereo.tobytes()
        else:
            processed = apply_robot_effect(samples, framerate, intensity)
            output_bytes = processed.tobytes()

        # 播放
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pa.get_format_from_width(sample_width),
            channels=n_channels,
            rate=framerate,
            output=True,
        )
        stream.write(output_bytes)
        stream.stop_stream()
        stream.close()
        pa.terminate()

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
