# -*- coding: utf-8 -*-
"""
全屏穿透覆盖层 (HUD Overlay)

作用: 在全屏透明画布上画"视觉角标框 / 扫描线 / 点击准星", 不接收鼠标事件。

三个技术点:
  1. 透明 —— Tk 没有逐像素 alpha, 用 -transparentcolor 色键抠掉背景 (和悬浮球同一套路);
  2. 穿透 —— 加 WS_EX_LAYERED | WS_EX_TRANSPARENT 扩展样式, 鼠标点击直接穿过去;
  3. 防截屏 —— SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) 让本窗口不进截图,
     否则截屏-画框-再截屏会套娃。

独立测试: python hud_overlay.py
"""
import ctypes
import ctypes.wintypes
import math
import os
import tkinter as tk
import time

# ═══════════════ 配色 ═══════════════
CHROMA_KEY = "#000001"      # 色键: 这个颜色会被抠成全透明 (别改成纯黑, 深色界面会误伤)
BG_DARK    = "#080E1A"
CYAN       = "#00F0FF"
CYAN_DIM   = "#0A6E7D"
AMBER      = "#FFB020"
RED        = "#FF3B5C"

WDA_EXCLUDEFROMCAPTURE = 0x00000011
WS_EX_LAYERED   = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE     = -20


# ═══════════════ 通用小工具 ═══════════════
def chamfer_points(x1, y1, x2, y2, cut):
    """切角矩形 (Chamfered Corners) 的顶点序列 —— 左上/右上/右下/左下各切一刀。

    Tk 画不了抗锯齿圆角, 硬切角反而更干净, 也更符合机甲 HUD 的味道。
    """
    cut = max(0, min(cut, (x2 - x1) // 2, (y2 - y1) // 2))
    return [
        x1 + cut, y1,
        x2 - cut, y1,
        x2,       y1 + cut,
        x2,       y2 - cut,
        x2 - cut, y2,
        x1 + cut, y2,
        x1,       y2 - cut,
        x1,       y1 + cut,
    ]


def get_hwnd(win):
    """拿到 Tk 窗口对应的顶级 Win32 句柄"""
    try:
        hwnd = win.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        return parent or hwnd
    except Exception:
        return None


def exclude_from_capture(win):
    """让窗口不出现在截图/录屏里 (Win10 2004+); 老系统静默失败。

    调试时可设环境变量 JARVIS_NO_CAPTURE_EXCLUDE=1 临时关掉, 方便截图排查。
    """
    if os.environ.get("JARVIS_NO_CAPTURE_EXCLUDE") == "1":
        return False
    hwnd = get_hwnd(win)
    if not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def make_click_through(win):
    """鼠标事件穿透: 点在覆盖层上等于点在桌面/下面的窗口上"""
    hwnd = get_hwnd(win)
    if not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                         style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        return True
    except Exception:
        return False


def _dim(hexc, f):
    f = max(0.0, f)
    r = min(255, int(int(hexc[1:3], 16) * f))
    g = min(255, int(int(hexc[3:5], 16) * f))
    b = min(255, int(int(hexc[5:7], 16) * f))
    return f"#{r:02x}{g:02x}{b:02x}"


def screen_scale(tk_width):
    """物理像素 → Tk 坐标的换算系数。

    截图和 pyautogui 用的是物理像素 (2560), 而 Tk 在"非 DPI 感知"进程里报的是
    缩放后的逻辑像素 (1707)。主程序开场就开了 DPI 感知, 所以这里通常返回 1.0;
    只有独立运行本文件时才会得到 1.5 这类值。
    """
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        hdc = u.GetDC(0)
        try:
            phys = g.GetDeviceCaps(hdc, 118)     # DESKTOPHORZRES
        finally:
            u.ReleaseDC(0, hdc)
        if phys and tk_width:
            return max(1.0, round(phys / float(tk_width), 3))
    except Exception:
        pass
    return 1.0


def active_window_rect():
    """当前活动窗口的物理像素矩形 (x, y, w, h); 拿不到返回 None"""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        r = ctypes.wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 60 or h <= 60:       # 明显不是正常窗口 (托盘/弹窗)
            return None
        return (r.left, r.top, w, h)
    except Exception:
        return None


# ═══════════════ 覆盖层 ═══════════════
class HudOverlay:
    """全屏透明覆盖层; 只在 Tk 主线程里被调用"""

    def __init__(self, root=None, manager=None):
        self.mgr = manager
        self._own_root = root is None

        if self._own_root:
            self.win = tk.Tk()
        else:
            self.win = tk.Toplevel(root)

        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=CHROMA_KEY)
        try:
            self.win.attributes("-transparentcolor", CHROMA_KEY)
        except Exception:
            pass

        self.SW = self.win.winfo_screenwidth()
        self.SH = self.win.winfo_screenheight()
        self.win.geometry(f"{self.SW}x{self.SH}+0+0")

        self.cv = tk.Canvas(self.win, width=self.SW, height=self.SH,
                            bg=CHROMA_KEY, highlightthickness=0, cursor="none")
        self.cv.pack(fill="both", expand=True)

        self.win.update_idletasks()
        make_click_through(self.win)
        exclude_from_capture(self.win)

        self.scale = screen_scale(self.SW)   # 物理像素 → Tk 坐标 (DPI 感知进程里恒为 1)
        self._boxes = []          # 手动/总线推来的框
        self._cross = []          # 准星
        self._seen_ts = set()     # 已消费过的总线目标时间戳, 防重复添加
        self._t0 = time.time()
        self._visible = False

    # ── 换算 ──
    def _tx(self, v):
        return int(v / self.scale)

    # ── 显隐 ──
    def show(self):
        if self._visible:
            return
        self._visible = True
        self.cv.delete("all")
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def hide(self):
        self._visible = False
        try:
            self.cv.delete("all")
            self.win.withdraw()
        except Exception:
            pass

    # ── 对外接口 ──
    def draw_scan_box(self, x, y, w, h, label="", ttl=5.0, color=None):
        """在物理坐标 (x, y, w, h) 上画一个切角视觉角标框"""
        if w <= 0 or h <= 0:
            return
        self.show()
        self._boxes.append({
            "x": self._tx(x), "y": self._tx(y),
            "w": self._tx(w), "h": self._tx(h),
            "label": str(label or ""),
            "color": color or CYAN,
            "ts": time.time(), "ttl": float(ttl),
        })
        del self._boxes[:-4]

    def draw_crosshair(self, x, y, ttl=1.6, color=None):
        """点击准星: 落在即将点击的位置上"""
        self.show()
        self._cross.append({
            "x": self._tx(x), "y": self._tx(y),
            "color": color or AMBER,
            "ts": time.time(), "ttl": float(ttl),
        })
        del self._cross[:-3]

    def draw_active_window(self, label="视觉扫描", ttl=4.0):
        """扫当前活动窗口 (截图时的默认目标); 拿不到就扫全屏"""
        rect = active_window_rect()
        if not rect:
            rect = (0, 0, int(self.SW * self.scale), int(self.SH * self.scale))
        self.draw_scan_box(*rect, label=label, ttl=ttl)

    def clear(self):
        self._boxes.clear()
        self._cross.clear()
        try:
            self.cv.delete("all")
        except Exception:
            pass

    def apply_snapshot(self, snap):
        """从 ui_bridge 快照里取目标框 (新增的才画, 已过期的不再画)"""
        if not snap:
            return
        for t in snap.get("targets") or []:
            key = (t["ts"], t["x"], t["y"])
            if key in self._seen_ts:
                continue
            self._seen_ts.add(key)
            if len(self._seen_ts) > 64:
                self._seen_ts = set(list(self._seen_ts)[-32:])
            self.draw_scan_box(t["x"], t["y"], t["w"], t["h"],
                               label=t.get("action", ""), ttl=t.get("ttl", 4.0))

    # ── 渲染 ──
    def render(self, t=None):
        if not self._visible:
            return
        now = time.time()
        t = t if t is not None else (now - self._t0)

        self._boxes = [b for b in self._boxes if now - b["ts"] < b["ttl"]]
        self._cross = [c for c in self._cross if now - c["ts"] < c["ttl"]]

        self.cv.delete("all")
        if not self._boxes and not self._cross:
            return

        for b in self._boxes:
            self._draw_box(b, now, t)
        for c in self._cross:
            self._draw_cross(c, now, t)

    def _draw_box(self, b, now, t):
        cv = self.cv
        age = now - b["ts"]
        fade_in = min(1.0, age / 0.18)
        fade_out = min(1.0, max(0.0, (b["ttl"] - age) / 0.6))
        alpha = fade_in * fade_out
        if alpha <= 0.02:
            return

        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        col = b["color"]
        x2, y2 = x + w, y + h

        # 呼吸 + 角标
        pulse = 0.72 + 0.28 * math.sin(t * 5.0)
        cut = max(6, min(18, int(min(w, h) * 0.08)))
        pts = chamfer_points(x, y, x2, y2, cut)
        cv.create_polygon(*pts, outline=_dim(col, 0.55 * alpha * pulse),
                          fill="", width=1)

        # 四角加粗 L 标
        seg = max(10, min(26, min(w, h) // 5))
        lw = 2
        for (px, py, dx, dy) in ((x, y, 1, 1), (x2, y, -1, 1),
                                 (x, y2, 1, -1), (x2, y2, -1, -1)):
            cv.create_line(px, py, px + dx * seg, py,
                           fill=_dim(col, alpha), width=lw)
            cv.create_line(px, py, px, py + dy * seg,
                           fill=_dim(col, alpha), width=lw)

        # 内部扫描线 (自上而下循环)
        if h > 20:
            span = max(1, h - 16)
            sy = y + 8 + (span * ((t * 0.55) % 1.0))
            cv.create_line(x + cut // 2, sy, x2 - cut // 2, sy,
                           fill=_dim(col, 0.30 * alpha), width=1)
            cv.create_line(x + cut // 2, sy, x2 - cut // 2, sy,
                           fill=_dim(col, 0.10 * alpha), width=4)

        # 顶部标签片
        label = b["label"]
        if label:
            tw = max(60, len(label) * 7 + 22)
            tw = min(tw, max(60, w))
            lx, ly = x, y - 18
            if ly < 2:
                ly = y + 4
            lpts = chamfer_points(lx, ly, lx + tw, ly + 16, 5)
            cv.create_polygon(*lpts, fill=_dim(BG_DARK, alpha * 0.92),
                              outline=_dim(col, 0.7 * alpha), width=1)
            cv.create_text(lx + 9, ly + 8, text=label.upper(), anchor="w",
                           fill=_dim(col, 0.95 * alpha), font=("Consolas", 8))

        # 尺寸标注 (右下角)
        if w > 90 and h > 60:
            cv.create_text(x2 - 4, y2 + 8, text=f"{w}×{h}", anchor="e",
                           fill=_dim(col, 0.45 * alpha), font=("Consolas", 8))

    def _draw_cross(self, c, now, t):
        cv = self.cv
        age = now - c["ts"]
        alpha = min(1.0, age / 0.1) * min(1.0, max(0.0, (c["ttl"] - age) / 0.4))
        if alpha <= 0.02:
            return
        x, y, col = c["x"], c["y"], c["color"]
        r = 14 + 3 * math.sin(t * 8)
        cv.create_oval(x - r, y - r, x + r, y + r,
                       outline=_dim(col, 0.8 * alpha), width=1)
        cv.create_oval(x - 3, y - 3, x + 3, y + 3,
                       fill=_dim(col, 0.9 * alpha), outline="")
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cv.create_line(x + dx * (r + 3), y + dy * (r + 3),
                           x + dx * (r + 10), y + dy * (r + 10),
                           fill=_dim(col, 0.7 * alpha), width=1)
        # 旋转弧
        a0 = (t * 120) % 360
        cv.create_arc(x - r - 7, y - r - 7, x + r + 7, y + r + 7,
                      start=a0, extent=70, style="arc",
                      outline=_dim(col, 0.5 * alpha), width=1)

    def run(self):
        """独立测试用"""
        self.show()
        self.draw_active_window("scan")
        self.draw_crosshair(self.SW // 2, self.SH // 2, ttl=5)
        end = time.time() + 6

        def _tick():
            self.render()
            if time.time() < end:
                self.win.after(30, _tick)
            else:
                self.win.destroy()

        _tick()
        self.win.mainloop()


# ═══════════════ 独立测试 ═══════════════
if __name__ == "__main__":
    HudOverlay().run()
    print("✔ 覆盖层测试结束")
