# -*- coding: utf-8 -*-
"""
贾维斯自定义音频包支持 (Audio Pack)
支持将自定义录制或生成的语音 (WAV/MP3) 放到 custom_voices/ 目录
当贾维斯触发对应事件或台词时，优先播放自定义录音！
"""
import os
import random
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CUSTOM_DIR = BASE / "custom_voices"

def ensure_custom_dir():
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    # 创建一个说明 README
    readme = CUSTOM_DIR / "README.txt"
    if not readme.exists():
        readme.write_text(
            "【自定义声音文件夹说明】\n\n"
            "你可以把自己喜欢的音效、角色台词（如钢铁侠Jarvis原声、动漫角色语音包）保存为 mp3 或 wav 放进来：\n"
            "1. 唤醒回应: wake_1.mp3, wake_2.mp3, wake.wav (听到唤醒词时会随机播一个，代替'我在')\n"
            "2. 确认提示: ok.mp3, yes.mp3 (执行普通命令成功时代替'好的')\n"
            "3. 没听清: pardon.mp3 (代替'没听清')\n"
            "4. 退出道别: bye.mp3 (代替'我先休息了')\n"
            "5. 开机语音: online.mp3 (开机自检完成时播放)\n"
            "6. 特定动作: 比如 open_app.mp3, shutdown.mp3, screenshot.mp3\n",
            encoding="utf-8"
        )

def _playback_envelope(pygame, file_path, interval=0.05):
    """从实际播放音频计算 RMS 包络，供全屏反应堆随语音音量律动。"""
    try:
        import numpy as np
        sound = pygame.mixer.Sound(str(file_path))
        samples = pygame.sndarray.samples(sound)
        mono = samples.astype(np.float32, copy=False)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)
        frequency = pygame.mixer.get_init()[0]
        chunk_size = max(1, int(frequency * interval))
        envelope = []
        for start in range(0, len(mono), chunk_size):
            chunk = mono[start:start + chunk_size]
            if len(chunk):
                rms = float(np.sqrt(np.mean(np.square(chunk))) / 32768.0)
                envelope.append(min(1.0, rms * 3.6))
        return envelope
    except Exception:
        return []


def play_audio_file(file_path):
    """跨格式稳定播放音频，并向 ui_bridge 推送真实输出电平。"""
    import ui_bridge
    clock = None
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        envelope = _playback_envelope(pygame, file_path)
        pygame.mixer.music.load(str(file_path))
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        started = time.monotonic()
        while pygame.mixer.music.get_busy():
            if envelope:
                index = min(len(envelope) - 1, int((time.monotonic() - started) / 0.05))
                ui_bridge.emit_audio_level(envelope[index])
            clock.tick(20)
        return True
    except Exception as e:
        print(f"[播放自定义音频失败] {e}")
        return False
    finally:
        ui_bridge.emit_audio_level(0.0)

def try_play_custom_action(action_or_key):
    """
    尝试按类别匹配并播放用户自定义音频
    匹配规则：custom_voices/<key>*.mp3 或 .wav
    例如 key='wake' 会匹配 wake.mp3, wake_1.wav 等并随机播放一个
    """
    if not CUSTOM_DIR.exists():
        return False
    
    matches = []
    for ext in ("*.mp3", "*.wav"):
        for f in CUSTOM_DIR.glob(f"{action_or_key}*{ext}"):
            matches.append(f)
            
    if matches:
        chosen = random.choice(matches)
        return play_audio_file(chosen)
    return False
