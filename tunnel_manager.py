# -*- coding: utf-8 -*-
"""出门版遥控的 Cloudflare 快速隧道管理，只依赖标准库。"""
import atexit
import os
import re
import shutil
import subprocess
import threading

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
RESTART_DELAYS = (2.0, 4.0)
MISSING_MESSAGE = (
    "未找到 cloudflared：请到 Cloudflare 官网下载 cloudflared.exe 放进 PATH，"
    "或在配置里填写 cloudflared_path。"
)


class TunnelManager:
    def __init__(self, port, password_getter, enabled=True, executable_path="",
                 which=None, popen=None, sleep=None, restart_delays=None):
        self.port = int(port)
        self.enabled = bool(enabled)
        self.executable_path = str(executable_path or "")
        self._password_getter = password_getter
        self._which = which or shutil.which
        self._popen = popen or subprocess.Popen
        self._sleep = sleep
        self.restart_delays = (tuple(RESTART_DELAYS) if restart_delays is None
                               else tuple(restart_delays))
        self.url = ""
        self.error = ""
        self._process = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._announced = set()
        self._exit_registered = False

    def start(self):
        if not self.enabled:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
        self._stop.clear()
        self.error = ""
        try:
            password = self._password_getter()
        except Exception as exc:
            self.error = "无法确认遥控口令，已停止出门隧道: {}".format(type(exc).__name__)
            print("[出门遥控] " + self.error, flush=True)
            return False
        if not password:
            self.error = "遥控口令为空，已停止出门隧道"
            print("[出门遥控] " + self.error, flush=True)
            return False
        executable = self._resolve()
        if not executable:
            self.error = MISSING_MESSAGE
            print("[出门遥控] " + self.error, flush=True)
            return False
        self._thread = threading.Thread(target=self._run, args=(executable,),
                                        name="cloudflared-tunnel", daemon=True)
        self._thread.start()
        if not self._exit_registered:
            atexit.register(self.stop)
            self._exit_registered = True
        return True

    def stop(self):
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except Exception:
                    pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            self._process = None
            self._thread = None
            self.url = ""

    def _resolve(self):
        if self.executable_path:
            path = os.path.expanduser(self.executable_path)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
            return ""
        try:
            return self._which("cloudflared") or ""
        except Exception:
            return ""

    def _command(self, executable):
        return [executable, "tunnel", "--url",
                "http://127.0.0.1:{}".format(self.port), "--no-autoupdate"]

    def _launch(self, executable):
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                  "bufsize": 1, "universal_newlines": True}
        if os.name == "nt":
            try:
                info = subprocess.STARTUPINFO()
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = info
            except AttributeError:
                pass
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return self._popen(self._command(executable), **kwargs)
        except OSError as exc:
            self.error = "cloudflared 启动失败: {}: {}".format(type(exc).__name__, exc)
            print("[出门遥控] " + self.error, flush=True)
            return None

    def _run(self, executable):
        attempts = 0
        while not self._stop.is_set():
            process = self._launch(executable)
            if process is None:
                return
            with self._lock:
                self._process = process
                self.url = ""
                stopped = self._stop.is_set()
            if stopped:
                self._terminate(process)
                with self._lock:
                    self._process = None
                return
            self._pump(process)
            try:
                code = process.wait(timeout=5)
            except Exception:
                code = None
            with self._lock:
                self._process = None
            if self._stop.is_set():
                return
            if attempts >= len(self.restart_delays):
                self.error = "cloudflared 连续退出 {} 次，已停止自动重启；请检查网络或可执行文件。".format(
                    attempts + 1)
                print("[出门遥控] " + self.error, flush=True)
                return
            delay = self.restart_delays[attempts]
            attempts += 1
            self.error = "cloudflared 意外退出（码 {}），{} 秒后重试（{}/{}）".format(
                code, delay, attempts, len(self.restart_delays))
            print("[出门遥控] " + self.error, flush=True)
            if self._sleep is not None:
                self._sleep(delay)
            else:
                self._stop.wait(delay)

    def _pump(self, process):
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                match = URL_RE.search(line)
                if not match:
                    continue
                url = match.group(0).rstrip("/")
                with self._lock:
                    if self._stop.is_set():
                        return
                    self.url = url
                    self.error = ""
                    fresh = url not in self._announced
                    self._announced.add(url)
                if fresh:
                    print("[出门遥控] 公网地址: {}（仅本机可见，请勿分享）".format(url), flush=True)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _terminate(process):
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
