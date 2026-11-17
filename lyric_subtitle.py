# -*- coding: utf-8 -*-
"""贾维斯桌面歌词式悬浮字幕。

只显示 ui_bridge 中 subtitle_final=True 的贾维斯回复；用户识别文本不显示。
窗口由 JarvisAppManager 的 Tk 主线程轮询驱动，也可独立运行本文件预览。
"""
import ctypes
import time
import tkinter as tk
import tkinter.font as tkfont

import ui_bridge

CHROMA = "#000001"
CYAN = "#48EDF7"
CYAN_GLOW = "#087A8A"
SHADOW = "#00242D"
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080


class LyricSubtitleWindow:
    """透明置顶歌词字幕；所有方法都只在 Tk 主线程调用。"""

    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=CHROMA)
        try:
            self.win.attributes("-transparentcolor", CHROMA)
        except Exception:
            pass

        self.SW = self.win.winfo_screenwidth()
        self.SH = self.win.winfo_screenheight()
        self.max_width = min(1560, max(520, int(self.SW * 0.78)))
        self.font = tkfont.Font(family="Microsoft YaHei UI", size=32, weight="bold")
        self.cv = tk.Canvas(
            self.win, width=self.max_width, height=120,
            bg=CHROMA, highlightthickness=0, bd=0,
        )
        self.cv.pack(fill="both", expand=True)

        self.enabled = True
        self.user_visible = True
        self.locked = False
        self.click_through = False
        self._user_pos = None
        self._drag = None
        self._text = ""
        self._subtitle_ts = 0.0
        self._alpha = 0.0
        self._last_tick = time.monotonic()
        self._geometry_key = None
        self._menu = tk.Menu(self.win, tearoff=0, font=("Microsoft YaHei UI", 10))

        self.cv.bind("<ButtonPress-1>", self._drag_start)
        self.cv.bind("<B1-Motion>", self._drag_move)
        self.cv.bind("<ButtonRelease-1>", self._drag_end)
        self.cv.bind("<Button-3>", self._popup_menu)
        self.win.update_idletasks()

    # ── 显示策略 ──
    def set_enabled(self, enabled):
        """全屏或隐藏界面时关闭桌面字幕，避免双份字幕。"""
        self.enabled = bool(enabled)
        if not self.enabled:
            self._hide_now()

    def toggle_visibility(self):
        self.user_visible = not self.user_visible
        if not self.user_visible:
            self._hide_now()
        self._last_tick = time.monotonic()
        return self.user_visible

    def _hide_now(self):
        self._alpha = 0.0
        self._last_tick = time.monotonic()
        try:
            self.win.withdraw()
        except Exception:
            pass

    def clear(self):
        self._text = ""
        self._subtitle_ts = 0.0
        self._hide_now()

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    # ── 总线快照 ──
    def apply_snapshot(self, snap, speaking=False):
        if not snap:
            return
        now = float(snap.get("ts") or time.time())
        text = str(snap.get("subtitle") or "").strip()
        is_final = bool(snap.get("subtitle_final"))
        subtitle_ts = float(snap.get("subtitle_ts") or 0.0)
        should_show = False

        if is_final and text:
            if text != self._text or abs(subtitle_ts - self._subtitle_ts) > 0.001:
                self._text = text
                self._subtitle_ts = subtitle_ts
                self._alpha = 0.0
                self._redraw()
            # 回复越长，基础停留越久；真实播放期间由 speaking 持续保活。
            hold = max(4.5, min(20.0, len(text) * 0.24))
            should_show = self.enabled and self.user_visible and (
                bool(speaking) or now - subtitle_ts < hold
            )
        elif text and subtitle_ts >= self._subtitle_ts:
            # 新的用户识别文本到来时，立即收起上一句回复。
            should_show = False

        self._animate(bool(should_show))

    def _animate(self, target_visible):
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._last_tick))
        self._last_tick = now
        target = 1.0 if target_visible and self._text else 0.0
        duration = 0.18 if target > self._alpha else 0.62
        step = dt / duration
        if self._alpha < target:
            self._alpha = min(target, self._alpha + step)
        elif self._alpha > target:
            self._alpha = max(target, self._alpha - step)

        if self._alpha <= 0.015:
            if self.win.winfo_viewable():
                self.win.withdraw()
            return
        if not self.win.winfo_viewable():
            self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", max(0.02, min(0.97, self._alpha * 0.97)))

    # ── 字幕绘制 ──
    def _redraw(self):
        self.cv.delete("all")
        if not self._text:
            return
        probe = self.cv.create_text(
            0, 0, text=self._text, anchor="nw", justify="center",
            width=self.max_width - 44, font=self.font,
        )
        bbox = self.cv.bbox(probe)
        self.cv.delete(probe)
        if not bbox:
            return
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        width = min(self.max_width, max(380, text_w + 46))
        height = text_h + 38
        self.cv.configure(width=width, height=height)
        center_x = width // 2
        text_width = width - 44

        # 深色外轮廓保证浅色壁纸上可读，外圈青色模拟柔和辉光。
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                       (-2, -2), (2, -2), (-2, 2), (2, 2)):
            self.cv.create_text(
                center_x + dx, 18 + dy, text=self._text, anchor="n",
                justify="center", width=text_width, font=self.font, fill=SHADOW,
            )
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.cv.create_text(
                center_x + dx, 18 + dy, text=self._text, anchor="n",
                justify="center", width=text_width, font=self.font, fill=CYAN_GLOW,
            )
        self.cv.create_text(
            center_x, 18, text=self._text, anchor="n", justify="center",
            width=text_width, font=self.font, fill=CYAN,
        )
        self._place_window(width, height)

    def _place_window(self, width, height):
        if self._user_pos is None:
            x = max(0, (self.SW - width) // 2)
            y = max(12, int(self.SH * 0.025))
        else:
            x, y = self._user_pos
        x = max(0, min(int(x), max(0, self.SW - width)))
        y = max(0, min(int(y), max(0, self.SH - height)))
        key = (width, height, x, y)
        if key != self._geometry_key:
            self._geometry_key = key
            self.win.geometry(f"{width}x{height}+{x}+{y}")

    # ── 拖动与右键设置 ──
    def _drag_start(self, event):
        if self.locked or self.click_through:
            return
        self._drag = (
            event.x_root - self.win.winfo_x(),
            event.y_root - self.win.winfo_y(),
        )

    def _drag_move(self, event):
        if not self._drag or self.locked:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self._user_pos = (x, y)
        self._place_window(self.win.winfo_width(), self.win.winfo_height())

    def _drag_end(self, _event):
        self._drag = None

    def reset_position(self):
        self._user_pos = None
        self._geometry_key = None
        self._redraw()

    def adjust_font(self, delta):
        size = max(20, min(54, int(self.font.cget("size")) + int(delta)))
        self.font.configure(size=size)
        self._geometry_key = None
        self._redraw()

    def toggle_lock(self):
        self.locked = not self.locked
        self._drag = None
        return self.locked

    def set_click_through(self, enabled):
        self.click_through = bool(enabled)
        try:
            hwnd = int(self.win.winfo_id())
            parent = ctypes.windll.user32.GetParent(hwnd)
            hwnd = parent or hwnd
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
            if self.click_through:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            self.click_through = False
        return self.click_through

    def toggle_click_through(self):
        return self.set_click_through(not self.click_through)

    def _popup_menu(self, event):
        if self.click_through:
            return
        self._menu.delete(0, "end")
        self._menu.add_command(
            label="解锁字幕位置" if self.locked else "锁定字幕位置",
            command=self.toggle_lock,
        )
        self._menu.add_command(label="恢复顶部居中", command=self.reset_position)
        self._menu.add_separator()
        self._menu.add_command(label="增大字号", command=lambda: self.adjust_font(2))
        self._menu.add_command(label="减小字号", command=lambda: self.adjust_font(-2))
        self._menu.add_command(label="启用鼠标穿透", command=lambda: self.set_click_through(True))
        self._menu.add_separator()
        self._menu.add_command(label="隐藏歌词字幕", command=self.toggle_visibility)
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()


def _demo():
    root = tk.Tk()
    root.withdraw()
    lyric = LyricSubtitleWindow(root)
    ui_bridge.emit_subtitle("贾维斯已上线，随时听候您的差遣。", True)
    started = time.monotonic()

    def tick():
        lyric.apply_snapshot(ui_bridge.snapshot(), speaking=time.monotonic() - started < 5.0)
        if time.monotonic() - started < 8.0:
            root.after(30, tick)
        else:
            lyric.destroy()
            root.destroy()

    tick()
    root.mainloop()


if __name__ == "__main__":
    _demo()
