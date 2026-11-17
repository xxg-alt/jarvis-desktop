# -*- coding: utf-8 -*-
"""
主控悬浮胶囊 (Control Capsule)

Tk 画不了抗锯齿圆角 (大圆角会出狗牙), 所以整颗胶囊用硬切角多边形:
  - 底色  #080E1A (深空深蓝)
  - 边框  1px #00F0FF (全息青)
  - 文字/波形全部落在不透明深色底内, 保证清晰

三种形态:
  待命/聆听  —— 小胶囊 (260×54): 状态灯 + 一字状态 + 音量波形
  忙 (思考/执行/视觉) —— 展开 (400×104): 加一行字幕
  等待确认 (GUARD) —— 展开 + 确认/取消两个切角按钮

交互: 拖动=移动; 单击=展开/收起; 双击=展开经典大中枢; 右键=菜单
独立测试: python capsule.py (会挂 15 秒演示, 走一遍六个状态)
"""
import math
import time
import tkinter as tk

import ui_bridge
from hud_overlay import (BG_DARK, CHROMA_KEY, RED, chamfer_points, _dim,
                         exclude_from_capture)

# ── 配色 ──
CYAN      = "#00F0FF"
CYAN_CORE = "#80FBFF"
GREEN     = "#00FFA3"
AMBER     = "#FFB020"
VIOLET    = "#B06CFF"
TEXT      = "#C8E4F0"
TEXT_DIM  = "#4E7B92"

STATE_COLOR = {
    ui_bridge.IDLE:       "#2E7F92",
    ui_bridge.LISTENING:  CYAN,
    ui_bridge.PERCEIVING: VIOLET,
    ui_bridge.THINKING:   AMBER,
    ui_bridge.ACTING:     GREEN,
    ui_bridge.GUARD:      RED,
}

W_SMALL, H_SMALL = 260, 54
W_BIG,   H_BIG   = 400, 100
W_GUARD, H_GUARD = 400, 140
CUT = 12                    # 切角尺寸
SUBTITLE_LIFE = 6.0         # 字幕显示多久后自动收回去


class CapsuleWindow:
    def __init__(self, manager=None, overlay=None):
        self.mgr = manager
        self.overlay = overlay

        self.win = tk.Toplevel(manager.root) if manager is not None else tk.Tk()
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
        self.w, self.h = W_SMALL, H_SMALL
        self.x = (self.SW - self.w) // 2
        self.y = self.SH - self.h - 90

        self.cv = tk.Canvas(self.win, width=self.w, height=self.h,
                            bg=CHROMA_KEY, highlightthickness=0, cursor="hand2")
        self.cv.pack(fill="both", expand=True)

        self.win.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        self.win.update_idletasks()
        exclude_from_capture(self.win)

        # 状态
        self.snap = {"state": ui_bridge.IDLE, "level": 0.0, "subtitle": "",
                     "guard": None, "targets": [], "seq": -1}
        self.t0 = time.time()
        self._drag = None
        self._moved = False
        self._force_big = False       # 单击手动展开
        self._btn_rects = {}          # {"ok": (x1,y1,x2,y2), "cancel": ...}

        # 事件
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Double-Button-1>", self._on_double)
        self.cv.bind("<Button-3>", self._on_menu)

        # 让总线知道"有界面在跑" (request_guard_confirm 依赖这个判断)
        ui_bridge.subscribe(self._on_bus)
        self._dirty = True

        self.win.deiconify()

    # ── 总线通知 (可能在业务线程!) : 只置脏, 不碰 Tk ──
    def _on_bus(self):
        self._dirty = True

    def apply_snapshot(self, snap):
        self.snap = snap
        if self.overlay is not None:
            self.overlay.apply_snapshot(snap)

    # ── 尺寸切换 ──
    def _layout_for(self, state, now):
        if state == ui_bridge.GUARD:
            return (W_GUARD, H_GUARD)
        if state in (ui_bridge.THINKING, ui_bridge.ACTING, ui_bridge.PERCEIVING):
            return (W_BIG, H_BIG)
        # 待命/聆听: 有新鲜字幕就短暂展开
        fresh = (now - self.snap.get("subtitle_ts", 0)) < SUBTITLE_LIFE
        if self.snap.get("subtitle") and (fresh or self._force_big):
            return (W_BIG, H_BIG)
        return (W_SMALL, H_SMALL)

    def _resize(self, w, h):
        if (w, h) == (self.w, self.h):
            return
        # 保持底部对齐, 水平居中于原位置
        cx = self.x + self.w // 2
        bottom = self.y + self.h
        self.w, self.h = w, h
        self.x = max(0, min(self.SW - w, cx - w // 2))
        self.y = max(0, bottom - h)
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y}")
        self.cv.config(width=w, height=h)

    # ── 渲染 ──
    def render(self, t=None, level=None):
        now = time.time()
        t = t if t is not None else (now - self.t0)
        state = self.snap.get("state", ui_bridge.IDLE)
        lvl = self.snap.get("level", 0.0) if level is None else level

        w, h = self._layout_for(state, now)
        self._resize(w, h)
        big = h >= H_BIG
        head_y = 34 if big else h // 2     # 顶行 (状态灯 / 状态字) 的中心线

        cv = self.cv
        cv.delete("all")
        self._btn_rects = {}

        col = STATE_COLOR.get(state, CYAN)

        # ── 外壳: 切角多边形 ──
        pts = chamfer_points(1, 1, w - 1, h - 1, CUT)
        cv.create_polygon(*pts, fill=BG_DARK, outline=CYAN, width=1)
        # 内侧细描边, 做出双层壳感
        ipts = chamfer_points(4, 4, w - 4, h - 4, CUT - 3)
        cv.create_polygon(*ipts, fill="", outline=_dim(CYAN, 0.18), width=1)

        # 左上/右下角激光装饰
        cv.create_line(6, 1, 28, 1, fill=_dim(col, 0.9), width=2)
        cv.create_line(w - 28, h - 2, w - 6, h - 2, fill=_dim(col, 0.9), width=2)

        # ── 状态灯 ──
        lx, ly = 26, head_y
        if state == ui_bridge.GUARD:
            led = RED if math.sin(t * 8) > 0 else _dim(RED, 0.35)
        elif state == ui_bridge.THINKING:
            led = _dim(AMBER, 0.55 + 0.45 * abs(math.sin(t * 3)))
        elif state == ui_bridge.LISTENING:
            led = _dim(CYAN, 0.55 + 0.45 * lvl)
        else:
            led = col
        cv.create_oval(lx - 9, ly - 9, lx + 9, ly + 9,
                       outline=_dim(col, 0.35), width=1)
        cv.create_oval(lx - 5, ly - 5, lx + 5, ly + 5, fill=led, outline="")
        # 旋转外环
        a0 = (t * (140 if state == ui_bridge.THINKING else 60)) % 360
        cv.create_arc(lx - 13, ly - 13, lx + 13, ly + 13,
                      start=a0, extent=90, style="arc",
                      outline=_dim(col, 0.55), width=1)

        # ── 状态文字 ──
        label = ui_bridge.STATE_LABELS.get(state, state)
        cv.create_text(50, head_y - 7 if big else head_y, text=label, anchor="w",
                       fill=_dim(col, 1.0), font=("Microsoft YaHei UI", 11, "bold"))
        if big:
            # 副行: 小字提示
            hint = {
                ui_bridge.IDLE: "SYSTEM ONLINE",
                ui_bridge.LISTENING: "正在收音 · 直接说指令",
                ui_bridge.PERCEIVING: "正在分析屏幕",
                ui_bridge.THINKING: "大模型推理中",
                ui_bridge.ACTING: "正在控制电脑",
                ui_bridge.GUARD: "高危操作, 请确认",
            }.get(state, "")
            cv.create_text(50, head_y + 9, text=hint, anchor="w",
                           fill=TEXT_DIM, font=("Consolas", 8))

        # ── 音量波形 (右上, 和状态字同一行) ──
        bars = 14
        bw, gap = 3, 3
        bx2 = w - 18
        bx1 = bx2 - bars * (bw + gap) + gap
        base_y = head_y
        active = state in (ui_bridge.LISTENING, ui_bridge.ACTING, ui_bridge.PERCEIVING)
        for i in range(bars):
            ph = math.sin(t * 7 + i * 0.7)
            amp = (lvl * 14) * (0.55 + 0.45 * abs(ph)) if active else (2 + 2 * abs(ph))
            bh = max(2, amp)
            c = _dim(CYAN_CORE if active else TEXT_DIM, 0.9 if active else 0.45)
            x1 = bx1 + i * (bw + gap)
            cv.create_rectangle(x1, base_y - bh, x1 + bw, base_y + bh,
                                fill=c, outline="")

        # ── 字幕行 (展开时): 与顶行用一条分隔线隔开 ──
        sub = (self.snap.get("subtitle") or "").strip()
        if big:
            cv.create_line(CUT + 4, 60, w - CUT - 4, 60,
                           fill=_dim(CYAN, 0.16), width=1)
            cv.create_text(CUT + 8, 60, text="▸", anchor="w",
                           fill=_dim(col, 0.6), font=("Consolas", 7))
        if big and sub:
            final = self.snap.get("subtitle_final", True)
            max_chars = 30
            shown = sub if len(sub) <= max_chars else "…" + sub[-max_chars:]
            cv.create_text(w // 2, 80, text=shown, anchor="center",
                           fill=_dim(TEXT, 0.95 if final else 0.7),
                           font=("Microsoft YaHei UI", 10))

        # ── 等待确认: 两个切角按钮 ──
        if state == ui_bridge.GUARD:
            by = h - 38
            self._draw_button("ok", "确认", w - 162, by, 68, 26, CYAN)
            self._draw_button("cancel", "取消", w - 86, by, 68, 26, RED)

    def _draw_button(self, key, text, x1, y1, bw, bh, col):
        cv = self.cv
        x2, y2 = x1 + bw, y1 + bh
        pts = chamfer_points(x1, y1, x2, y2, 6)
        cv.create_polygon(*pts, fill=_dim(col, 0.16), outline=col, width=1)
        cv.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=text,
                       fill=col, font=("Microsoft YaHei UI", 10, "bold"))
        self._btn_rects[key] = (x1, y1, x2, y2)

    # ── 事件 ──
    def _hit(self, x, y):
        for key, (x1, y1, x2, y2) in self._btn_rects.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _on_press(self, e):
        key = self._hit(e.x, e.y)
        if key and self.snap.get("state") == ui_bridge.GUARD:
            ui_bridge.resolve_guard(key == "ok")
            self._drag = None
            return
        self._drag = (e.x_root - self.x, e.y_root - self.y)
        self._moved = False

    def _on_drag(self, e):
        if not self._drag:
            return
        dx, dy = self._drag
        # 必须夹在屏幕内: 否则能一路拖到屏幕外, 松手后窗口还在但完全看不见 = "球消失了"
        self.x = max(0, min(self.SW - self.w, e.x_root - dx))
        self.y = max(0, min(self.SH - self.h, e.y_root - dy))
        self._moved = True
        self.win.geometry(f"+{self.x}+{self.y}")

    def _on_release(self, e):
        # 单击 (没拖动) = 手动展开/收起
        if self._drag and not self._moved:
            self._force_big = not self._force_big
        self._drag = None

    def _on_double(self, e):
        if self.mgr is not None:
            self.mgr.use_classic_ui()

    def _on_menu(self, e):
        m = tk.Menu(self.win, tearoff=0, bg=BG_DARK, fg=TEXT,
                    activebackground=_dim(CYAN, 0.3),
                    activeforeground="#000000", bd=0)
        m.add_command(label="展开经典大中枢", command=lambda: self.mgr and self.mgr.use_classic_ui())
        m.add_command(label="切换为桌面悬浮球", command=lambda: self.mgr and self.mgr.use_orb_ui())
        m.add_separator()
        m.add_command(label="隐藏胶囊", command=self.hide)
        m.add_command(label="退出贾维斯", command=lambda: self.mgr and self.mgr.quit_app())
        m.post(e.x_root, e.y_root)

    # ── 显隐 ──
    def show(self):
        try:
            # 兜底: 万一位置在屏幕外 (老版本拖出去过), 显示时拉回来
            nx = max(0, min(self.SW - self.w, self.x))
            ny = max(0, min(self.SH - self.h, self.y))
            if (nx, ny) != (self.x, self.y):
                self.x, self.y = nx, ny
                self.win.geometry(f"{self.w}x{self.h}+{nx}+{ny}")
            self.win.deiconify()
            self.win.attributes("-topmost", True)
        except Exception:
            pass

    def hide(self):
        try:
            self.win.withdraw()
        except Exception:
            pass


# ═══════════════ 独立测试: 走一遍六个状态 ═══════════════
if __name__ == "__main__":
    cap = CapsuleWindow()

    script = [
        (0.0,  ui_bridge.LISTENING, 0.55, "贾维斯, 帮我打开微信"),
        (2.5,  ui_bridge.THINKING,  0.10, "正在理解你的指令"),
        (5.0,  ui_bridge.ACTING,    0.30, "已打开微信"),
        (7.5,  ui_bridge.PERCEIVING, 0.05, "正在扫描当前窗口"),
        (10.0, ui_bridge.IDLE,      0.02, "已打开微信"),
    ]
    end = time.time() + 14
    i = 0

    def tick():
        global i
        now = time.time()
        if i < len(script) and now - start >= script[i][0]:
            _, st, lv, sub = script[i]
            ui_bridge.emit_state(st)
            ui_bridge.emit_audio_level(lv)
            ui_bridge.emit_subtitle(sub, True)
            i += 1
        cap.apply_snapshot(ui_bridge.snapshot())
        cap.render()
        if now < end:
            cap.win.after(33, tick)
        else:
            cap.hide()
            cap.win.destroy()

    start = time.time()
    tick()
    cap.win.mainloop()
    print("✔ 胶囊测试结束")
