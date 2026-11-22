# -*- coding: utf-8 -*-
"""Windows 任务栏图标治理。

现象: 从源码启动时是 pythonw.exe 在跑 .py, Windows 给窗口用的是 .py 的通用图标
(白色文档 + Python 标记), 任务栏上看起来像"桌面上又多了一个脚本"。
再加上 web_capsule / web_fullscreen 各是一个子进程, 图标会成倍出现。

做法:
    set_app_id()                 统一 AppUserModelID, 让所有窗口归到同一个任务栏按钮
    apply_icon(hwnd)             把窗口图标换成 assets/jarvis.ico (WM_SETICON)
    hide_from_taskbar(hwnd)      摘掉任务栏按钮 (WS_EX_TOOLWINDOW), 悬浮胶囊用
    setup_window(title, hide=…)  按标题轮询找窗口再一次性做完 (窗口创建时还拿不到 HWND)

非 Windows 平台全部空转, 不影响云主机上的评测。
"""
import ctypes
import os
import sys
import threading
import time

APP_ID = "Jarvis.Desktop.HUD"

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040


def _icon_candidates():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "assets", "jarvis.ico")]
    # PyInstaller: 数据文件解包在 _MEIPASS, 源码目录另外再兜一层
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "assets", "jarvis.ico"))
        cands.append(os.path.join(os.path.dirname(sys.executable), "assets", "jarvis.ico"))
    return [p for p in cands if p and os.path.exists(p)]


def set_app_id(app_id: str = APP_ID):
    """给当前进程一个稳定的 AppUserModelID; 所有窗口归到同一个任务栏按钮下。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def _load_icon():
    if os.name != "nt":
        return 0
    u = ctypes.windll.user32
    for path in _icon_candidates():
        for flags in (LR_LOADFROMFILE | LR_DEFAULTSIZE, LR_LOADFROMFILE):
            try:
                h = u.LoadImageW(0, ctypes.c_wchar_p(path), IMAGE_ICON, 0, 0, flags)
                if h:
                    return h
            except Exception:
                continue
    return 0


def apply_icon(hwnd) -> bool:
    """把窗口的大/小图标都换成贾维斯图标。"""
    if os.name != "nt" or not hwnd:
        return False
    hicon = _load_icon()
    if not hicon:
        return False
    try:
        u = ctypes.windll.user32
        u.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        u.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        return True
    except Exception:
        return False


def hide_from_taskbar(hwnd) -> bool:
    """摘掉任务栏按钮 (保留窗口本身; 也会从 Alt+Tab 里消失)。已经摘掉则空转。"""
    if os.name != "nt" or not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        target = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
        if style == target:
            return True
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, target)
        return True
    except Exception:
        return False


def is_hidden(hwnd) -> bool:
    """当前是否已经不在任务栏。"""
    if os.name != "nt" or not hwnd:
        return False
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        return bool(style & WS_EX_TOOLWINDOW) and not (style & WS_EX_APPWINDOW)
    except Exception:
        return False


def _find_windows(title: str):
    """按标题前缀找到所有顶层窗口。"""
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(300)
            u.GetWindowTextW(hwnd, buf, 300)
            if buf.value and buf.value.startswith(title):
                found.append(hwnd)
        except Exception:
            pass
        return True

    u.EnumWindows(proc(_cb), 0)
    return found


def setup_window(title: str, hide: bool = False, icon: bool = True,
                 timeout: float = 8.0, poll: float = 0.5) -> bool:
    """等窗口出现, 然后持续守护它的图标/任务栏状态, 直到窗口销毁。

    为什么要"持续": 实测 pywebview(winforms) 在窗口显示之后会自己再补一次
    WS_EX_APPWINDOW, 一次性设置会被它盖回去, 于是任务栏照样多个按钮。
    这个线程很轻 (每 0.5s 读一次窗口样式, 已经对了就不写), 窗口一销毁就退出。
    """
    if os.name != "nt":
        return False
    u = ctypes.windll.user32
    found, deadline = [], time.time() + timeout
    while time.time() < deadline and not found:
        found = _find_windows(title)
        if not found:
            time.sleep(poll)

    state = {}          # hwnd -> [icon_done, hide_done]
    empty_since = None
    while True:
        live = []
        for hwnd in found:
            try:
                if not u.IsWindow(hwnd):
                    continue
            except Exception:
                continue
            st = state.setdefault(hwnd, [False, False])
            if icon and not st[0]:
                st[0] = apply_icon(hwnd)
            if hide and not st[1]:
                st[1] = hide_from_taskbar(hwnd)
            # 被盖回去就再摘一次 (只在真的被盖回去时写)
            if hide and not is_hidden(hwnd):
                hide_from_taskbar(hwnd)
            live.append(hwnd)
        found = live
        for hwnd in _find_windows(title):
            if hwnd not in found:
                found.append(hwnd)
        if not found:
            if empty_since is None:
                empty_since = time.time()
            elif time.time() - empty_since > 4.0:
                return False
        else:
            empty_since = None
        time.sleep(poll)


def setup_window_async(title: str, hide: bool = False, icon: bool = True):
    """后台线程版本: 在 webview.start() 之前调用, 不阻塞子进程启动。"""
    threading.Thread(target=setup_window, args=(title, hide, icon),
                     name="win-taskbar", daemon=True).start()
