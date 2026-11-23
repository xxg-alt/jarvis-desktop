# -*- coding: utf-8 -*-
"""
Web 版主控胶囊 (PyWebView)

为什么是独立进程:
    pywebview 强制要求 webview.start() 跑在主线程, 而贾维斯的 Tk 主循环也占着主线程。
    所以这里把 WebView 放进子进程, 用本地 socket 传 JSON, 父进程 (Tk) 完全不受影响:
        父进程 jarvis_gui.py  ──snapshot──▶  子进程 web_capsule.py (PyWebView)
                              ◀──事件────

子进程启动方式:
    源码:  python web_capsule.py --child <port>
    打包:  Jarvis.exe --child <port>
"""
import ctypes
import ctypes.wintypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as JsPushTimeout
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
BASE = APP_DIR  # 日志、子进程入口等可写路径仍放在程序目录


def _taskkill_path() -> str:
    """定位 taskkill.exe；受限环境的 PATH 里可能没有 System32。"""
    import shutil
    hit = shutil.which("taskkill")
    if hit:
        return hit
    return str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "taskkill.exe")


def _child_python() -> str:
    """子进程解释器必须自带 pywebview。

    sys.executable 并不可信：双击 .py 文件、或在 IDE 里跑的时候，它会指向
    一个没装 pywebview 的解释器（实测系统 Python312 就是这种），子进程一启动
    就 ModuleNotFoundError 秒退，胶囊永远起不来，而主进程还以为"已经切过去了"，
    屏幕上于是什么都不剩。源码模式优先用项目自带的 venv。
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    for exe in ("pythonw.exe", "python.exe"):
        cand = BASE / ".venv" / "Scripts" / exe
        if cand.is_file():
            return str(cand)
    return sys.executable


def _accept_alive(srv, proc, timeout, label="Web 胶囊"):
    """等子进程连上来，但子进程一死就立刻失败，不要干等到超时。

    旧写法是裸 srv.accept()：子进程一秒都没活过，这里仍会阻塞满 12 秒，
    用户看到的就是"按完 ESC 界面没了，然后卡住半天"。
    """
    deadline = time.time() + timeout
    while True:
        try:
            srv.settimeout(0.2)
            conn, _ = srv.accept()
            return conn
        except socket.timeout:
            if proc.poll() is not None:
                raise RuntimeError(f"{label}子进程已退出 (code={proc.returncode})")
            if time.time() >= deadline:
                raise RuntimeError(f"{label}子进程启动超时")
WEB_DIR = RESOURCE_DIR / "web_ui"
if not WEB_DIR.is_dir():
    WEB_DIR = APP_DIR / "web_ui"

# 窗口尺寸 (WebView2 强制 DPR=1, 这里的数值就是窗口物理像素)
SIZES = {
    "IDLE":       (84, 84),
    "LISTENING":  (360, 84),
    "THINKING":   (420, 132),
    "ACTING":     (420, 132),
    "PERCEIVING": (420, 132),
    "GUARD":      (420, 168),
}
BOTTOM_MARGIN = 92        # 物理像素 (WebView2 已强制 DPR=1, CSS 像素 == 物理像素)
BG_COLOR = "#070D18"

VK_LBUTTON = 0x01
MONITOR_DEFAULTTONEAREST = 2


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


# ═══════════════════════════════════════════════════════
#  一、子进程: 真正显示窗口
# ═══════════════════════════════════════════════════════
class _Child:
    def __init__(self, port):
        self.port = port
        self.win = None
        self.sock = None
        self.scale = 1.0
        self.size = SIZES["IDLE"]
        self.x = 0
        self.y = 0
        self._last_state = None
        self._lock = threading.Lock()
        self._pending = None                  # 最新一份待推送数据
        self._pending_evt = threading.Event()  # 有新数据待推送
        self._page_ready = threading.Event()   # 页面已加载完 (JS 会回调 report)
        self._user_pos = None                  # 用户拖到的位置 (None = 还没拖过, 用默认位置)
        self._dragging = False
        self._deferred_size = None             # 拖动期间收到的尺寸变化, 松手后再应用
        self._active = True                     # 窗口是否可见 (用于暂停前端 rAF)
        self._js_active = None
        # evaluate_js 是无限期阻塞调用: 统一走单线程执行器 + 超时看门狗,
        # 与全屏 HUD 的处理保持一致。之前这里是裸调用, 页面一断就把推送线程永久卡死。
        self._js_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cap-js")
        self._js_lock = threading.Lock()
        self._pending_lock = threading.Lock()   # 主线程写 _pending / 推送线程读

    # ── Win32 小工具 ──
    def _wait_native(self, timeout=15.0):
        """pywebview 的 native 对象要等窗口真正建好才有 (约 1 秒)"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = getattr(self.win, "native", None)
            if n is not None:
                return n
            time.sleep(0.1)
        raise RuntimeError("WebView 窗口句柄迟迟没有就绪")

    def _hwnd(self):
        return int(self.win.native.Handle.ToInt64())

    def _screen_phys(self):
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)

    def _rect(self):
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._hwnd(), ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def _apply_win32(self):
        h = self._hwnd()
        u = ctypes.windll.user32
        # 1) DWM 平滑圆角 + 深色标题栏
        try:
            dwm = ctypes.windll.dwmapi
            corner = ctypes.c_int(2)     # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(h, 33, ctypes.byref(corner), 4)
            dark = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(h, 20, ctypes.byref(dark), 4)
        except Exception:
            pass
        # 2) 截图/录屏时排除本窗口 (调试可设 JARVIS_NO_CAPTURE_EXCLUDE=1 临时关闭)
        if os.environ.get("JARVIS_NO_CAPTURE_EXCLUDE") != "1":
            try:
                u.SetWindowDisplayAffinity(h, 0x00000011)
            except Exception:
                pass
        # 3) 不抢任务栏
        try:
            ex = u.GetWindowLongW(h, -20)
            u.SetWindowLongW(h, -20, ex | 0x80)   # WS_EX_TOOLWINDOW
        except Exception:
            pass

    def _work_area(self, x, y):
        """取 (x, y) 所在显示器的可用区域 (已扣除任务栏); 多屏时也能正确判断"""
        u = ctypes.windll.user32
        try:
            # HMONITOR 是指针, 必须声明 restype, 否则 64 位下会被截断成无效句柄
            u.MonitorFromPoint.argtypes = [_POINT, ctypes.c_ulong]
            u.MonitorFromPoint.restype = ctypes.c_void_p
            u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
            u.GetMonitorInfoW.restype = ctypes.c_bool
            hmon = u.MonitorFromPoint(_POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if hmon and u.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        sw, sh = self._screen_phys()
        return 0, 0, sw, sh

    def _clamp(self, x, y, w, h):
        """把窗口矩形夹进所在显示器的可用区域 —— 任何情况下都不许跑到屏幕外"""
        ax, ay, aw, ah = self._work_area(x + w // 2, y + h // 2)
        x = max(ax, min(ax + max(0, aw - w), int(x)))
        y = max(ay, min(ay + max(0, ah - h), int(y)))
        return x, y

    def _ensure_onscreen(self):
        """兜底: 若窗口已跑到屏幕外, 拉回最近的可用区域"""
        try:
            x, y, w, h = self._rect()
            nx, ny = self._clamp(x, y, w, h)
            if (nx, ny) != (x, y):
                self._set_rect(nx, ny, w, h)
                self.x, self.y = nx, ny
                if self._user_pos:
                    self._user_pos = (nx, ny)
        except Exception:
            pass

    def _target_rect(self, cw, ch):
        """算出目标位置: 没拖过就居中贴底; 拖过就在原地向上生长, 底边不动"""
        w, h = int(cw), int(ch)
        if self._user_pos is None:
            sw, sh = self._screen_phys()
            tx = int((sw - w) / 2)
            ty = int(sh - h - BOTTOM_MARGIN)
        else:
            px, py = self._user_pos
            cw0, ch0 = self.size
            tx = int(px + (cw0 - w) / 2)     # 水平方向保持原来的中心
            ty = int(py + ch0 - h)           # 底边固定, 往上长
        return self._clamp(tx, ty, w, h)

    def _place(self, cw, ch, animate=False):
        """cw/ch 就是最终窗口尺寸 (WebView2 已强制 DPR=1, CSS 像素 == 物理像素)"""
        w, h = int(cw), int(ch)
        tx, ty = self._target_rect(cw, ch)

        if not animate:
            self._set_rect(tx, ty, w, h)
            self.x, self.y, self.size = tx, ty, (cw, ch)
            return

        # 简易补间: 从当前矩形平滑长到目标矩形
        steps = 9
        x0, y0, w0, h0 = self._rect()
        for i in range(1, steps + 1):
            p = i / steps
            e = 1 - (1 - p) ** 3
            nw = int(w0 + (w - w0) * e)
            nh = int(h0 + (h - h0) * e)
            nx = int(x0 + (tx - x0) * e)
            ny = int(y0 + (ty - y0) * e)
            self._set_rect(nx, ny, nw, nh)
            time.sleep(0.018)
        self.x, self.y, self.size = tx, ty, (cw, ch)

    def _set_rect(self, x, y, w, h):
        """直接改窗口矩形 (物理像素)。注意必须声明 argtypes, 否则 HWND(-1) 会被截断导致调用失败。"""
        u = ctypes.windll.user32
        hwnd = ctypes.wintypes.HWND(self._hwnd())
        u.SetWindowPos.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_bool
        HWND_TOPMOST = ctypes.wintypes.HWND(-1)
        SWP_NOACTIVATE = 0x0010
        u.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y), int(w), int(h), SWP_NOACTIVATE)

    # ── 生命周期 ──
    def boot(self):
        try:
            self._wait_native()
        except Exception as e:
            print(f"[胶囊] 窗口初始化失败: {e}", flush=True)
            return
        # DPI 缩放: pywebview 的坐标/尺寸是逻辑像素, 会被乘上这个系数
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(self._hwnd())
            self.scale = round(dpi / 96.0, 3) or 1.0
        except Exception:
            self.scale = 1.0
        self._apply_win32()

        self.size = SIZES["IDLE"]
        sw, sh = self._screen_phys()
        print(f"[胶囊] 屏幕={sw}x{sh} dpi_scale={self.scale}", flush=True)
        self._place(*self.size)
        rt = self._rect()
        self.x, self.y = rt[0], rt[1]
        print(f"[胶囊] scale={self.scale} rect={rt}", flush=True)

        threading.Thread(target=self._js_worker, daemon=True).start()
        threading.Thread(target=self._net_loop, daemon=True).start()

    def _connect(self):
        for _ in range(60):
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                s.settimeout(None)
                return s
            except OSError:
                time.sleep(0.15)
        return None

    def _net_loop(self):
        self.sock = self._connect()
        if not self.sock:
            print("[胶囊] 连不上主进程, 独立演示模式", flush=True)
            return
        self.sock.sendall(b'{"type":"hello"}\n')
        buf = b""
        while True:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._handle(json.loads(line.decode("utf-8")))
            except Exception:
                break
        print("[胶囊] 与主进程断开, 自动关闭", flush=True)
        try:
            self.win.destroy()
        except Exception:
            pass

    def _handle(self, msg):
        kind = msg.get("type")
        if kind == "visible":
            try:
                self._active = bool(msg.get("v"))
                if self._active:
                    self.win.show()
                    self._ensure_onscreen()   # 显示时若在屏幕外就拉回来
                else:
                    self.win.hide()
                # 隐藏窗口 native hide 不一定触发 visibilitychange, 显式通知前端停画
                self._pending_evt.set()
            except Exception:
                pass
            return
        if kind != "state":
            return
        snap = msg.get("snap") or {}
        st = snap.get("state") or "IDLE"
        if st != self._last_state:
            print(f"[胶囊] 收到状态 {st}", flush=True)
        payload = {
            "state": st,
            "level": snap.get("level", 0.0),
            "subtitle": snap.get("subtitle", ""),
            "guard": snap.get("guard"),
        }
        js = f"window.JarvisUI && window.JarvisUI.update({json.dumps(payload, ensure_ascii=False)})"
        # evaluate_js 只能在页面加载完后调用, 否则会卡死接收线程 —— 交给专门的推送线程
        with self._pending_lock:
            self._pending = {"js": js, "state": st, "size": SIZES.get(st, SIZES["LISTENING"])}
            self._pending_evt.set()
            self._last_state = st

    def _push_js(self, js, what="状态"):
        """带看门狗的 evaluate_js：超时即放弃该调用线程并更换执行器，推送管线继续存活。"""
        executor = self._js_exec
        fut = executor.submit(self.win.evaluate_js, js)
        try:
            fut.result(timeout=8)
            return True
        except JsPushTimeout:
            print(f"[胶囊] 页面{what}注入超过8秒未完成，跳过本次以保活推送线程", flush=True)
            with self._js_lock:
                if self._js_exec is executor:
                    self._js_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cap-js")
            return False
        except Exception as e:
            print(f"[胶囊] 推送到页面失败: {e}", flush=True)
            return False

    def _sync_page_animation(self):
        """页面隐藏时显式停掉 Canvas 循环，避免 WebView2 后台空转。"""
        if self._active == self._js_active:
            return
        js = ("window.JarvisUI && window.JarvisUI.setAnimating("
              + ("true" if self._active else "false") + ")")
        if self._push_js(js, "动画"):
            self._js_active = self._active

    def _js_worker(self):
        """单线程串行推送到页面, 避免多线程 evaluate_js 互相阻塞"""
        self._page_ready.wait(20)
        while True:
            self._pending_evt.wait(0.5)
            with self._pending_lock:
                job, self._pending = self._pending, None
                self._pending_evt.clear()
            if not job:
                self._sync_page_animation()
                continue
            if not self._push_js(job["js"], "状态"):
                # 注入失败（页面断开/渲染进程卡住）不能让推送线程陪葬：
                # 继续下一轮，等页面恢复或由主进程重启子进程。
                continue
            self._sync_page_animation()
            st = job["state"]
            if job["size"] != self.size:
                if self._dragging:
                    # 拖着手柄时别改窗口大小, 松手后再展开, 否则会跟鼠标抢位置
                    self._deferred_size = job["size"]
                    continue
                try:
                    self._place(*job["size"], animate=True)
                    print(f"[胶囊] 切到 {st} rect={self._rect()}", flush=True)
                except Exception as e:
                    print(f"[胶囊] 缩放失败 {e}", flush=True)

    def _send(self, obj):
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception:
            pass

    # ── 给 JS 调的 API ──
    def api_resolve_guard(self, ok):
        self._send({"type": "guard", "ok": bool(ok)})
        return True

    def api_move_by(self, dx, dy):
        """备用: JS 侧相对位移 (正常拖动走 start_drag, 这里只做兜底)"""
        try:
            x, y, w, h = self._rect()
            nx, ny = self._clamp(x + int(dx * self.scale), y + int(dy * self.scale), w, h)
            self._set_rect(nx, ny, w, h)
            self.x, self.y = nx, ny
            self._user_pos = (nx, ny)
        except Exception:
            pass
        return True

    def api_start_drag(self, offx, offy):
        """接管拖动: 由 Python 直接跟鼠标指针走, 比 JS 每帧 IPC 更跟手, 也不会拖丢"""
        if self._dragging:
            return True
        self._dragging = True
        self._user_pos = (self.x, self.y)
        threading.Thread(target=self._drag_loop,
                         args=(int(offx), int(offy)), daemon=True).start()
        return True

    def _drag_loop(self, offx, offy):
        """按住左键期间用 GetCursorPos 跟随; 松手或拖太久就结束"""
        u = ctypes.windll.user32
        p = _POINT()
        t0 = time.time()
        try:
            while time.time() - t0 < 60:
                if not (u.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                    break
                u.GetCursorPos(ctypes.byref(p))
                _, _, w, h = self._rect()
                nx, ny = self._clamp(p.x - offx, p.y - offy, w, h)
                self._set_rect(nx, ny, w, h)
                self.x, self.y = nx, ny
                self._user_pos = (nx, ny)
                time.sleep(0.008)
        except Exception as e:
            print(f"[胶囊] 拖动异常: {e}", flush=True)
        finally:
            self._dragging = False
            print(f"[胶囊] 拖动结束 pos=({self.x},{self.y})", flush=True)
            if self._deferred_size:
                size, self._deferred_size = self._deferred_size, None
                try:
                    self._place(*size, animate=True)
                except Exception:
                    pass

    def api_open_classic(self):
        self._send({"type": "classic"})
        return True

    def api_close(self):
        self._send({"type": "quit"})
        try:
            self.win.destroy()
        except Exception:
            pass
        return True

    def api_ready(self):
        return True

    def api_report(self, info):
        print(f"[胶囊] 页面视口 {info}", flush=True)
        self._page_ready.set()
        return True


class _Api:
    """暴露给 JS: window.pywebview.api.*"""

    def __init__(self, child):
        self._c = child

    def resolve_guard(self, ok):
        return self._c.api_resolve_guard(ok)

    def move_by(self, dx, dy):
        return self._c.api_move_by(dx, dy)

    def start_drag(self, offx, offy):
        return self._c.api_start_drag(offx, offy)

    def open_classic(self):
        return self._c.api_open_classic()

    def close(self):
        return self._c.api_close()

    def ready(self):
        return self._c.api_ready()

    def report(self, info):
        return self._c.api_report(info)


def run_child(port):
    # WebView2 在这台机器上把 DPR 算成 2.175 (应为 1.5), 导致页面视口只有窗口的 1/2。
    # 强制缩放为 1, 让 CSS 像素 == 物理像素, 排版才可控。
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--force-device-scale-factor=1"
    import webview

    html = WEB_DIR / "index.html"
    if not html.exists():
        print(f"[胶囊] 找不到页面: {html}", flush=True)
        return

    child = _Child(int(port))
    child.win = webview.create_window(
        "Jarvis Capsule",
        url=str(html),
        width=SIZES["IDLE"][0],
        height=SIZES["IDLE"][1],
        frameless=True,
        on_top=True,
        resizable=False,
        min_size=(20, 20),
        easy_drag=False,          # 自绘拖动, 关掉 pywebview 内置的整窗拖拽
        background_color=BG_COLOR,
        js_api=_Api(child),
    )
    # 悬浮胶囊不该占任务栏: 否则任务栏会多出一个 .py 通用图标的按钮
    try:
        import win_taskbar
        win_taskbar.set_app_id()
        win_taskbar.setup_window_async("Jarvis Capsule", hide=True)
    except Exception:
        pass
    webview.start(child.boot, debug=False)


# ═══════════════════════════════════════════════════════
#  二、父进程侧: 启动子进程 + 推状态
# ═══════════════════════════════════════════════════════
class WebCapsuleHost:
    """在 Tk 进程里使用: 起子进程、推快照、收事件"""

    def __init__(self, on_classic=None):
        self.on_classic = on_classic
        self.proc = None
        self.conn = None
        self.server = None
        self.alive = False
        self._stop = False
        self._last_seq = -1
        self.visible = True

    # ── 启停 ──
    def start(self, timeout=12.0):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(timeout)
        port = srv.getsockname()[1]
        self.server = srv

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--web-child", str(port)]
        else:
            cmd = [_child_python(), str(BASE / "web_capsule.py"), "--child", str(port)]

        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # 子进程日志单独落盘, 出问题好排查
        self._log = open(BASE / "web-capsule.log", "a", encoding="utf-8",
                         errors="ignore", buffering=1)
        self._log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动子进程 port={port} ====\n")
        self.proc = subprocess.Popen(cmd, cwd=str(BASE), creationflags=creation,
                                     stdout=self._log, stderr=subprocess.STDOUT)

        try:
            self.conn = _accept_alive(srv, self.proc, timeout)
            self.conn.settimeout(None)
        except socket.timeout:
            self.stop()
            raise RuntimeError("Web 胶囊子进程启动超时")
        except Exception:
            self.stop()
            raise

        self.alive = True
        self.conn.recv(4096)   # hello
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._tx_loop, daemon=True).start()
        return True

    def stop(self):
        self._stop = True
        self.alive = False
        for s in (self.conn, self.server):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self.conn = self.server = None
        if self.proc is not None:
            # 不加 poll() 判断：子进程"已经死了"恰恰是最需要清理残留 WebView2 的时候。
            # kill_tree 对失效 PID 会安静返回 0，不会误伤。
            # 必须用 proc_tree：taskkill /T 按 PPID 构树，遇到失效/复用的 PID
            # 会把主进程误判成子树成员一起强杀（实测"按 ESC 后程序自己没了"）。
            try:
                from proc_tree import kill_tree
                kill_tree(self.proc.pid)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
        self.proc = None

    # ── 收发 ──
    def set_visible(self, visible):
        """显示/隐藏胶囊。返回是否真的送达。

        以前这里是静默 return：连接断了也当作发送成功，调用方于是以为
        "已经切到胶囊了"，而屏幕上其实什么都没有 —— 用户看到的就是"退出"。
        """
        if not self.conn:
            return False
        self.visible = bool(visible)
        try:
            self.conn.sendall((json.dumps({"type": "visible", "v": bool(visible)}) + "\n")
                              .encode("utf-8"))
            return True
        except Exception:
            self.alive = False
            return False

    def _tx_loop(self):
        import ui_bridge
        last_state = None
        while not self._stop and self.conn:
            try:
                snap = ui_bridge.snapshot()
                if snap["state"] != last_state:
                    last_state = snap["state"]
                    print(f"[Web胶囊] 推送 {snap['state']} seq={snap['seq']}", flush=True)
                if snap["seq"] != self._last_seq or snap["state"] == "GUARD":
                    self._last_seq = snap["seq"]
                    # 字幕超过 8 秒就不再显示, 免得旧话一直挂着
                    fresh = (time.time() - float(snap.get("subtitle_ts") or 0)) < 8.0
                    msg = {"type": "state", "snap": {
                        "state": snap["state"],
                        "level": round(float(snap["level"]), 3),
                        "subtitle": snap["subtitle"] if fresh else "",
                        "guard": snap["guard"],
                    }}
                    self.conn.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            except Exception:
                break
            time.sleep(0.06 if self.visible else 0.5)

    def _rx_loop(self):
        import ui_bridge
        buf = b""
        while not self._stop and self.conn:
            try:
                chunk = self.conn.recv(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line.decode("utf-8"))
                    if msg.get("type") == "guard":
                        ui_bridge.resolve_guard(bool(msg.get("ok")))
                    elif msg.get("type") == "classic" and self.on_classic:
                        self.on_classic()
            except Exception:
                break
        self.alive = False


# ═══════════════ 独立运行 ═══════════════
if __name__ == "__main__":
    if "--child" in sys.argv:
        run_child(sys.argv[sys.argv.index("--child") + 1])
    else:
        print("用法: python web_capsule.py --child <port>   (或由 jarvis_gui.py 自动拉起)")
        print("想单独看效果: python jarvis_gui.py --ui web")
