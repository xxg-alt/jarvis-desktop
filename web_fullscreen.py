# -*- coding: utf-8 -*-
"""J.A.R.V.I.S. 全屏 PyWebView 战术全息指挥中枢。

与灵动岛相同，WebView 在独立子进程中运行，避免和 Tk 主循环争用主线程。
主进程独占 jarvis 后端、配置写入和 ui_bridge；子进程只负责显示与发送用户事件。

源码子进程: python web_fullscreen.py --child <port>
打包子进程: Jarvis.exe --fullscreen-child <port>
独立预览:   python web_fullscreen.py --preview
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
BASE = APP_DIR  # 日志、子进程入口等可写路径仍放在程序目录
WEB_DIR = RESOURCE_DIR / "web_fullscreen_ui"
if not WEB_DIR.is_dir():
    # 打包时若没有把 web_fullscreen_ui 收进 _MEIPASS，回退到 exe 同级的资源目录。
    WEB_DIR = APP_DIR / "web_fullscreen_ui"
BG_COLOR = "#040914"
VOICE_LIST = ["云希", "云扬", "晓晓", "晓伊", "云健"]


def _line(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


class _Child:
    """WebView 子进程。socket 断开即退出，不在后台留下无主窗口。"""

    def __init__(self, port=0, preview=False):
        self.port = int(port or 0)
        self.preview = bool(preview)
        self.win = None
        self.sock = None
        self._page_ready = threading.Event()
        self._pending_evt = threading.Event()
        self._pending = None
        self._pending_lock = threading.Lock()
        self._stop = False
        self._active = True
        self._js_active = None
        self._demo_config = {
            "providers": ["DeepSeek", "通义千问 (Qwen)", "智谱清言 (GLM)"],
            "voices": VOICE_LIST,
            "ai_provider": "DeepSeek",
            "api_key": "",
            "tts_voice": "云希",
            "robot_voice": 0.7,
            "vision_enabled": True,
            "always_listen": True,
        }

    def boot(self):
        threading.Thread(target=self._js_worker, daemon=True).start()
        if self.preview:
            threading.Thread(target=self._demo_loop, daemon=True).start()
        else:
            threading.Thread(target=self._net_loop, daemon=True).start()

    def _connect(self):
        for _ in range(80):
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                sock.settimeout(None)
                return sock
            except OSError:
                time.sleep(0.15)
        return None

    def _net_loop(self):
        self.sock = self._connect()
        if not self.sock:
            print("[全屏HUD] 无法连接主进程", flush=True)
            try:
                self.win.destroy()
            except Exception:
                pass
            return
        self.sock.sendall(b'{"type":"hello"}\n')
        buf = b""
        while not self._stop:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if raw.strip():
                        self._handle(json.loads(raw.decode("utf-8")))
            except Exception as exc:
                print(f"[全屏HUD] 连接中断: {exc}", flush=True)
                break
        try:
            self.win.destroy()
        except Exception:
            pass

    def _handle(self, msg):
        kind = msg.get("type")
        try:
            if kind == "visible":
                self._active = bool(msg.get("v"))
                self.win.show() if self._active else self.win.hide()
                self._pending_evt.set()   # 让推送线程显式暂停/恢复前端 rAF
                return
            if kind == "focus":
                self.win.show()
                try:
                    self.win.restore()
                except Exception:
                    pass
                return
            if kind == "close":
                self.win.destroy()
                return
        except Exception:
            return
        if kind != "sync":
            return

        # evaluate_js 串行执行；等待期间只合并最新状态，但日志逐条保留。
        with self._pending_lock:
            pending = self._pending or {}
            old_logs = pending.get("logs") or []
            pending.update(msg)
            new_logs = msg.get("logs") or []
            if old_logs or new_logs:
                pending["logs"] = (old_logs + new_logs)[-300:]
            self._pending = pending
        self._pending_evt.set()

    def _sync_page_animation(self):
        """窗口收起/隐藏时显式停掉前端 Canvas 主循环。"""
        if self._active == self._js_active:
            return
        try:
            if self._active:
                # 重新显示时先走页面自己的 wake()，把 calm/折叠状态一并恢复；
                # 页面还没就绪时退回只恢复动画。
                self.win.evaluate_js(
                    "window.JarvisUI && window.JarvisUI.wake ? window.JarvisUI.wake() : "
                    "(window.JarvisHUD && window.JarvisHUD.setAnimating(true))"
                )
            else:
                self.win.evaluate_js(
                    "window.JarvisHUD && window.JarvisHUD.setAnimating(false)"
                )
            self._js_active = self._active
        except Exception as exc:
            print(f"[全屏HUD] 暂停/恢复绘制失败: {exc}", flush=True)

    def _js_worker(self):
        self._page_ready.wait(25)
        while not self._stop:
            self._pending_evt.wait(0.5)
            with self._pending_lock:
                job, self._pending = self._pending, None
                self._pending_evt.clear()
            if not job:
                self._sync_page_animation()
                continue
            payload = {
                "snap": job.get("snap") or {},
                "config": job.get("config"),
                "output": job.get("output"),
                "metrics": job.get("metrics") or {},
                "logs": job.get("logs") or [],
                "notice": job.get("notice"),
                "preview": self.preview,
            }
            js = (
                "window.JarvisHUD && window.JarvisHUD.update("
                + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
                + ")"
            )
            try:
                self.win.evaluate_js(js)
            except Exception as exc:
                print(f"[全屏HUD] 推送页面失败: {exc}", flush=True)
            self._sync_page_animation()

    def _send(self, obj):
        if self.preview:
            self._preview_event(obj)
            return
        if not self.sock:
            return
        try:
            self.sock.sendall(_line(obj))
        except Exception:
            pass

    def _preview_event(self, obj):
        kind = obj.get("type")
        if kind == "command":
            text = str(obj.get("text") or "").strip()
            self._handle({"type": "sync", "notice": {
                "ok": True, "message": f"视觉预览不会执行指令：{text}"}})
        elif kind == "config":
            patch = obj.get("patch") or {}
            for key in ("ai_provider", "tts_voice", "robot_voice", "vision_enabled", "always_listen"):
                if key in patch:
                    if key in ("robot_voice", "vision_enabled", "always_listen"):
                        self._demo_config[key] = bool(patch[key])
                    else:
                        self._demo_config[key] = patch[key]
            self._handle({"type": "sync", "config": self._demo_config,
                          "notice": {"ok": True, "message": "预览配置已在页面内更新，未写入磁盘"}})
        elif kind == "guard":
            self._handle({"type": "sync", "notice": {
                "ok": True, "message": "预览安全确认已响应，未执行任何系统动作"}})
        elif kind == "collapse":
            self._handle({"type": "sync", "notice": {
                "ok": True, "message": "完整应用中，此按钮会收起为灵动岛"}})
        elif kind == "hide":
            try:
                self.win.destroy()
            except Exception:
                pass

    # ── 以下由 _Api 暴露给 JavaScript ──
    def api_report(self, info):
        print(f"[全屏HUD] 页面就绪 {info}", flush=True)
        self._page_ready.set()
        return True

    def api_command(self, text):
        text = str(text or "").strip()[:4000]
        if text:
            self._send({"type": "command", "text": text})
        return True

    def api_save_config(self, patch):
        if not isinstance(patch, dict):
            return False
        allowed = {"ai_provider", "api_key", "tts_voice", "robot_voice",
                   "vision_enabled", "always_listen"}
        clean = {k: v for k, v in patch.items() if k in allowed}
        if clean:
            self._send({"type": "config", "patch": clean})
        return True

    def api_collapse(self):
        self._send({"type": "collapse"})
        return True

    def api_hide(self):
        self._send({"type": "hide"})
        return True

    def api_resolve_guard(self, ok):
        self._send({"type": "guard", "ok": bool(ok)})
        return True

    def _demo_loop(self):
        """不加载 jarvis、不打开麦克风的纯视觉演示。"""
        import math
        import random
        self._page_ready.wait(10)
        sequence = [
            ("IDLE", "全屏战术指挥大厅已就绪。Sir，随时听候您的差遣。"),
            ("LISTENING", "贾维斯，帮我分析当前系统状态"),
            ("THINKING", "正在理解指令，调度大模型推理核心……"),
            ("ACTING", "遥测数据已汇总，所有核心系统运行正常。"),
            ("PERCEIVING", "正在扫描当前窗口与视觉目标……"),
            ("GUARD", "高危操作需要确认。此预览不会执行任何系统动作。"),
        ]
        started = time.monotonic()
        last_stage = -1
        logs = deque(maxlen=8)
        while not self._stop:
            elapsed = time.monotonic() - started
            stage = int(elapsed / 4.5) % len(sequence)
            state, output = sequence[stage]
            stage_changed = stage != last_stage
            if stage_changed:
                logs.append(f"{time.strftime('%H:%M:%S')}  STATE  ->  {state}")
                last_stage = stage
            if state == "THINKING":
                level = 0.08 + 0.03 * math.sin(elapsed * 5)
            elif state in ("LISTENING", "ACTING"):
                level = 0.28 + 0.24 * abs(math.sin(elapsed * 5.7))
            else:
                level = 0.04 + 0.03 * abs(math.sin(elapsed * 2.1))
            snap = {
                "state": state,
                "level": level,
                "subtitle": output,
                "subtitle_final": state in ("IDLE", "ACTING"),
                "guard": {"desc": output} if state == "GUARD" else None,
                "speaking": state == "ACTING",
            }
            metrics = {
                "cpu": 18 + 9 * abs(math.sin(elapsed * 0.7)),
                "memory": 46 + 5 * math.sin(elapsed * 0.31),
                "disk": 51.2,
            }
            self._handle({
                "type": "sync", "snap": snap, "config": self._demo_config,
                "output": output, "metrics": metrics,
                "logs": [logs[-1]] if stage_changed else [],
            })
            time.sleep(0.05 if not self.preview else 0.04 + random.random() * 0.005)


class _Api:
    def __init__(self, child):
        self._c = child

    def report(self, info):
        return self._c.api_report(info)

    def command(self, text):
        return self._c.api_command(text)

    def save_config(self, patch):
        return self._c.api_save_config(patch)

    def collapse(self):
        return self._c.api_collapse()

    def hide(self):
        return self._c.api_hide()

    def resolve_guard(self, ok):
        return self._c.api_resolve_guard(ok)


def run_child(port=0, preview=False):
    # 页面按 1.6 倍缩放显示；在 2560×1600 屏上得到约 1600×1000 的 CSS 视口。
    # 这样固定 px 的字号/面板比 1 倍 DPI 大约 60%，又不会像跟随系统 2.175 倍那样拥挤。
    # 需要微调时可用环境变量 JARVIS_UI_SCALE 覆盖。
    ui_scale = os.environ.get("JARVIS_UI_SCALE", "1.6")
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--force-device-scale-factor={ui_scale}"
    )
    import webview

    html = WEB_DIR / "index.html"
    if not html.exists():
        raise FileNotFoundError(f"找不到全屏 HUD 页面: {html}")
    child = _Child(port, preview)
    child.win = webview.create_window(
        "J.A.R.V.I.S. 战术全息指挥中枢",
        url=str(html),
        width=1440,
        height=900,
        min_size=(1024, 640),
        fullscreen=True,
        frameless=True,
        on_top=not preview,
        resizable=True,
        easy_drag=False,
        background_color=BG_COLOR,
        js_api=_Api(child),
    )
    webview.start(child.boot, debug=False)


class FullscreenHUDWindow:
    """主进程中的全屏窗口控制器，接口供 JarvisAppManager 使用。"""

    def __init__(self, manager):
        self.mgr = manager
        self.proc = None
        self.conn = None
        self.server = None
        self.alive = False
        self.visible = False
        self._stop = False
        self._io_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._last_seq = -1
        self._last_metrics = 0.0
        self._metrics = {"cpu": 0.0, "memory": 0.0, "disk": 0.0}
        self._output = "全屏战术指挥大厅已就绪。Sir，随时听候您的差遣。"
        self._output_dirty = True
        self._config_dirty = True
        self._config = None
        self._pending_logs = deque(maxlen=300)
        self._notice = None
        self._log = None

    # ── 生命周期 ──
    def start(self, timeout=15.0):
        if self.alive and self.proc and self.proc.poll() is None:
            return True
        self._stop = False
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(timeout)
        self.server = srv
        port = srv.getsockname()[1]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--fullscreen-child", str(port)]
        else:
            cmd = [sys.executable, str(BASE / "web_fullscreen.py"), "--child", str(port)]
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._log = open(BASE / "web-fullscreen.log", "a", encoding="utf-8",
                         errors="ignore", buffering=1)
        self._log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 全屏HUD port={port} ====\n")
        self.proc = subprocess.Popen(cmd, cwd=str(BASE), creationflags=creation,
                                     stdout=self._log, stderr=subprocess.STDOUT)
        try:
            self.conn, _ = srv.accept()
            self.conn.settimeout(None)
            hello = self.conn.recv(4096)
            if b"hello" not in hello:
                raise RuntimeError("全屏 HUD 子进程握手失败")
        except Exception:
            self.stop()
            raise
        self.alive = True
        self._last_seq = -1
        self._config_dirty = True
        self._output_dirty = True
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._tx_loop, daemon=True).start()
        return True

    def show(self):
        self.start()
        self.visible = True
        self._send({"type": "visible", "v": True})
        self.focus_force()

    def hide(self):
        self.visible = False
        self._send({"type": "visible", "v": False})

    def lift(self):
        if self.visible:
            self._send({"type": "focus"})

    def focus_force(self):
        self.lift()

    def stop(self):
        self._stop = True
        self.alive = False
        self.visible = False
        for sock in (self.conn, self.server):
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
        self.conn = self.server = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None
        try:
            if self._log:
                self._log.close()
        except Exception:
            pass
        self._log = None

    def render(self, *_args):
        """渲染由 WebView 的 requestAnimationFrame 独立完成。"""
        return None

    # ── 主进程向页面提供的数据 ──
    def set_output(self, text):
        text = str(text or "").strip()
        if not text:
            return
        with self._data_lock:
            self._output = text
            self._output_dirty = True

    def append_log(self, text):
        lines = str(text or "").splitlines()
        if not lines:
            return
        with self._data_lock:
            self._pending_logs.extend(line[-2000:] for line in lines)

    def _read_config(self):
        import jarvis
        from llm_brain import LLM_PROVIDERS
        cfg = jarvis.CONFIG
        return {
            "providers": list(LLM_PROVIDERS.keys()),
            "voices": VOICE_LIST,
            "ai_provider": cfg.get("ai_provider", "DeepSeek"),
            "api_key": cfg.get("api_key", ""),
            "tts_voice": cfg.get("tts_voice", "云希"),
            "robot_voice": cfg.get("robot_voice", 0),
            "vision_enabled": bool(cfg.get("vision_enabled", True)),
            "always_listen": bool(cfg.get("always_listen", True)),
        }

    def _sample_metrics(self):
        import psutil
        disk = Path.home().anchor or "C:\\"
        self._metrics = {
            "cpu": round(float(psutil.cpu_percent()), 1),
            "memory": round(float(psutil.virtual_memory().percent), 1),
            "disk": round(float(psutil.disk_usage(disk).percent), 1),
        }

    def _send(self, obj):
        conn = self.conn
        if not conn:
            return
        try:
            with self._io_lock:
                conn.sendall(_line(obj))
        except Exception:
            self.alive = False

    def _tx_loop(self):
        import jarvis
        import ui_bridge
        while not self._stop and self.conn:
            try:
                snap = ui_bridge.snapshot()
                now = time.time()
                metrics_dirty = False
                if now - self._last_metrics >= 1.8:
                    self._last_metrics = now
                    self._sample_metrics()
                    metrics_dirty = True
                with self._data_lock:
                    logs = list(self._pending_logs)
                    self._pending_logs.clear()
                    notice, self._notice = self._notice, None
                    output_dirty = self._output_dirty
                    self._output_dirty = False
                    config_dirty = self._config_dirty
                    self._config_dirty = False
                    output = self._output
                changed = snap["seq"] != self._last_seq
                if changed or logs or notice or output_dirty or config_dirty or metrics_dirty:
                    self._last_seq = snap["seq"]
                    config = None
                    if config_dirty or self._config is None:
                        self._config = self._read_config()
                        config = self._config
                    fresh = now - float(snap.get("subtitle_ts") or 0) < 30.0
                    payload = {
                        "state": snap["state"],
                        "level": round(float(snap["level"]), 4),
                        "subtitle": snap["subtitle"] if fresh else "",
                        "subtitle_final": snap["subtitle_final"],
                        "guard": snap["guard"],
                        "speaking": bool(jarvis.is_speaking()),
                    }
                    self._send({
                        "type": "sync", "snap": payload, "config": config,
                        "flags": snap.get("flags") or {
                            "vision_enabled": bool(jarvis.CONFIG.get("vision_enabled", True)),
                            "always_listen": bool(jarvis.CONFIG.get("always_listen", True)),
                        },
                        "output": output if output_dirty else None,
                        "metrics": self._metrics, "logs": logs, "notice": notice,
                    })
            except Exception as exc:
                print(f"[全屏HUD] 状态推送异常: {exc}", flush=True)
                time.sleep(0.2)
            time.sleep(0.06 if self.visible else 0.5)

    # ── 页面事件：仍由主进程执行原后端 ──
    def _rx_loop(self):
        import ui_bridge
        buf = b""
        while not self._stop and self.conn:
            try:
                chunk = self.conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    msg = json.loads(raw.decode("utf-8"))
                    kind = msg.get("type")
                    if kind == "command":
                        text = str(msg.get("text") or "").strip()[:4000]
                        if text:
                            threading.Thread(target=self._run_command, args=(text,), daemon=True).start()
                    elif kind == "config":
                        self._apply_config(msg.get("patch") or {})
                    elif kind == "guard":
                        ui_bridge.resolve_guard(bool(msg.get("ok")))
                    elif kind == "collapse":
                        self.mgr.root.after(0, self.mgr.switch_to_orb)
                    elif kind == "hide":
                        self.mgr.root.after(0, self.mgr.hide_all)
            except Exception as exc:
                if not self._stop:
                    print(f"[全屏HUD] 事件接收异常: {exc}", flush=True)
                break
        self.alive = False

    @staticmethod
    def _run_command(text):
        import jarvis
        jarvis.handle_command(text)

    def _notify(self, message, ok=True):
        with self._data_lock:
            self._notice = {"message": str(message), "ok": bool(ok)}

    def _apply_config(self, patch):
        import jarvis
        from llm_brain import LLM_PROVIDERS, clean_api_key
        if not isinstance(patch, dict):
            return
        clean = {}
        try:
            if "ai_provider" in patch:
                provider = str(patch["ai_provider"])
                if provider not in LLM_PROVIDERS:
                    raise ValueError("未知的大模型服务商")
                clean["ai_provider"] = provider
            if "tts_voice" in patch:
                voice = str(patch["tts_voice"])
                if voice not in VOICE_LIST:
                    raise ValueError("未知的音色")
                clean["tts_voice"] = voice
            if "robot_voice" in patch:
                clean["robot_voice"] = 0.7 if bool(patch["robot_voice"]) else 0
            if "vision_enabled" in patch:
                clean["vision_enabled"] = bool(patch["vision_enabled"])
            if "always_listen" in patch:
                clean["always_listen"] = bool(patch["always_listen"])
            if "api_key" in patch:
                key = str(patch["api_key"] or "").strip()
                if key and not clean_api_key(key):
                    raise ValueError("密钥格式不对：应为一串不含空格和换行的可见字符")
                clean["api_key"] = key
            if not clean:
                return

            cfg_file = jarvis.USER_CFG_FILE
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
            data.update(clean)
            cfg_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            jarvis.CONFIG.update(clean)
            try:
                import ui_bridge
                ui_bridge.update_flags(
                    vision_enabled=bool(jarvis.CONFIG.get("vision_enabled", True)),
                    always_listen=bool(jarvis.CONFIG.get("always_listen", True)),
                )
            except Exception:
                pass
            if "api_key" in clean:
                os.environ["AI_API_KEY"] = clean["api_key"]
                self.set_output(
                    f"AI 密钥已成功配置！当前接入服务商：{jarvis.CONFIG.get('ai_provider', 'DeepSeek')}。"
                    "随时向我提问吧，Sir。"
                )
            with self._data_lock:
                self._config = None
                self._config_dirty = True
            if "tts_voice" in clean or "robot_voice" in clean:
                voice = jarvis.CONFIG.get("tts_voice", "云希")
                mechanical = bool(jarvis.CONFIG.get("robot_voice", 0))
                self._notify(f"声线已切换为 {'机械音 · ' if mechanical else ''}{voice}")
            elif "api_key" in clean:
                self._notify("AI 核心配置已保存")
            elif "vision_enabled" in clean:
                self._notify("视觉感知已开启" if clean["vision_enabled"] else "视觉感知已关闭")
            elif "always_listen" in clean:
                self._notify("自动接管已开启" if clean["always_listen"] else "自动接管已关闭")
            elif "ai_provider" in clean:
                self._notify(f"服务商已切换为 {clean['ai_provider']}")
        except Exception as exc:
            self._notify(str(exc), False)
            self.set_output(str(exc))


if __name__ == "__main__":
    if "--child" in sys.argv:
        run_child(int(sys.argv[sys.argv.index("--child") + 1]))
    elif "--preview" in sys.argv:
        run_child(preview=True)
    else:
        print("视觉预览: python web_fullscreen.py --preview")
        print("子进程:   python web_fullscreen.py --child <port>")
