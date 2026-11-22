# -*- mode: python ; coding: utf-8 -*-
import json
from pathlib import Path

import vosk
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

project_dir = Path(SPECPATH)
vosk_dir = Path(vosk.__file__).parent
vosk_dlls = [(str(vosk_dir / name), "vosk") for name in (
    "libvosk.dll", "libstdc++-6.dll", "libgcc_s_seh-1.dll", "libwinpthread-1.dll",
) if (vosk_dir / name).exists()]

# Windows 自带语音识别 (WinRT 投影) 是运行时动态加载的, 需要显式收集
try:
    winrt_hidden = collect_submodules("winrt")
    winrt_binaries = collect_dynamic_libs("winrt")
except Exception as e:
    print(f"[Jarvis.spec] winrt 收集失败 (系统语音将不可用): {e}")
    winrt_hidden, winrt_binaries = [], []

# 打包前剔除 api_key：绝不把个人密钥打进 dist/ 和安装包。
# 用户在自己的机器上通过界面填写即可，那个 key 只存在本机 _internal/config.json。
src_cfg = project_dir / "config.json"
pack_cfg = project_dir / "build" / "packcfg" / "config.json"
if src_cfg.exists():
    cfg_data = json.loads(src_cfg.read_text(encoding="utf-8"))
    if cfg_data.pop("api_key", None):
        print("[Jarvis.spec] 已从打包用的 config 中移除 api_key")
    pack_cfg.parent.mkdir(parents=True, exist_ok=True)
    pack_cfg.write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")
else:
    pack_cfg = src_cfg

# 本地 whisper 识别 (faster-whisper + CTranslate2) 用到的原生库要显式收集
try:
    fw_bins = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("av") \
        + collect_dynamic_libs("onnxruntime")
    fw_data = collect_data_files("faster_whisper")
    fw_hidden = collect_submodules("faster_whisper") + collect_submodules("ctranslate2")
except Exception as e:
    print(f"[Jarvis.spec] whisper 依赖收集失败 (本地识别将不可用): {e}")
    fw_bins, fw_data, fw_hidden = [], [], []

# Web 胶囊界面 (pywebview + pythonnet)
try:
    wv_data = collect_data_files("webview")
    wv_hidden = ["webview.platforms.winforms", "clr", "clr_loader", "pythonnet"]
    wv_bins = collect_dynamic_libs("clr_loader")
except Exception as e:
    print(f"[Jarvis.spec] pywebview 收集失败 (Web 胶囊将不可用): {e}")
    wv_data, wv_hidden, wv_bins = [], [], []

a = Analysis(
    [str(project_dir / "jarvis_gui.py")],
    pathex=[str(project_dir)],
    binaries=vosk_dlls + winrt_binaries + fw_bins + wv_bins,
    datas=[
        (str(pack_cfg), "."),
        (str(project_dir / "custom_voices"), "custom_voices"),
        (str(project_dir / "models"), "models"),
        (str(project_dir / "assets" / "splash"), "assets/splash"),
        (str(project_dir / "web_ui"), "web_ui"),
        (str(project_dir / "web_fullscreen_ui"), "web_fullscreen_ui"),
    ] + fw_data + wv_data,
    hiddenimports=["app_index", "llm_brain", "jarvis_memory", "smart_launcher", "win_speech",
                   "whisper_engine", "faster_whisper", "tokenizers",
                   "huggingface_hub", "ui_bridge", "capsule", "hud_overlay",
                   "web_capsule", "web_fullscreen", "lyric_subtitle",
                   "audio_device", "diagnostics", "system_tools", "proc_tree",
                   "rag_engine", "web_tools", "agent_core", "onnxruntime", "pypdf", "docx"] + winrt_hidden + fw_hidden + wv_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="Jarvis",
)
