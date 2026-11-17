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
        asyncio.run(_synthesize(text, voice, tmp_path))
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
        asyncio.run(_synthesize(text, voice, mp3_path))

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
