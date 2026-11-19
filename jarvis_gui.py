# -*- coding: utf-8 -*-
"""
贾维斯 3D 双模系统 v3.2：真·全屏全息指挥中枢 + 3D 桌面悬浮球
- 解决高分屏 (2.5K/4K) 缩放导致的“窗口缩在左上角”问题（开启 Windows 硬件级 DPI 感知与真全屏覆盖）
- 两侧面板（左侧控制罗盘、右侧硬件日志）支持独立自由折叠 / 展开！
- 中间超大 3D 聚能反应堆 + 全息大字回答 + 打字输入
- 按 Esc 键随时平滑收回为桌面 3D 全息悬浮球
"""
import ctypes
import json
import math
import os
import queue
import random
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
import psutil

# 开启 Windows 硬件级高 DPI 适配，确保 2560x1600 (2.5K) 等屏幕真·100% 满屏覆盖
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
LOG_FILE = BASE / "jarvis-gui.log"
PREVIEW = "--preview" in sys.argv


def escape_to_orb(mgr):
    """ESC 的统一语义：先打断播报，再收起界面。

    只收起不闭嘴的话，屏幕变了但声音还在，用户会以为按键没生效，
    接着反复按、反复点 —— 这就是"按 ESC 卡死"的体感来源。
    """
    try:
        import jarvis
        if jarvis.is_speaking():
            jarvis.stop_speaking()
            print("[界面] ESC：已打断当前播报", flush=True)
    except Exception:
        pass
    try:
        mgr.switch_to_orb()
    except Exception as exc:
        print(f"[界面] ESC 收起失败: {exc}", flush=True)

# ── PyWebView 子进程入口 (不启动 Tk, 避免两个 GUI 主循环冲突) ──
if "--fullscreen-child" in sys.argv:
    try:
        from web_fullscreen import run_child
        run_child(int(sys.argv[sys.argv.index("--fullscreen-child") + 1]))
    finally:
        os._exit(0)

if "--web-child" in sys.argv:
    try:
        from web_capsule import run_child
        run_child(sys.argv[sys.argv.index("--web-child") + 1])
    finally:
        os._exit(0)

# 界面风格: web = PyWebView 胶囊 (默认), new = Tk 胶囊, classic = 老界面
UI_STYLE = "web"
for _i, _a in enumerate(sys.argv):
    if _a == "--ui" and _i + 1 < len(sys.argv):
        UI_STYLE = sys.argv[_i + 1].strip().lower()
if os.environ.get("JARVIS_UI"):
    UI_STYLE = os.environ["JARVIS_UI"].strip().lower()
if UI_STYLE not in ("web", "new", "classic"):
    UI_STYLE = "web"

import ui_bridge

# ── 启动画面 ──
if not PREVIEW and not os.environ.get("SKIP_SPLASH"):
    try:
        from splash import SplashScreen
        SplashScreen().run()
    except Exception as _e:
        print(f"[启动画面跳过] {_e}")

# ---------- 日志重定向 ----------
log_queue = queue.Queue()
class OutputTee:
    def write(self, s):
        if not s: return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(s)
        except Exception: pass
        log_queue.put(s)
    def flush(self): pass

sys.stdout = OutputTee()
sys.stderr = OutputTee()

# 接上日志文件之后立刻装好崩溃/线程看门狗：
# pythonw 没有控制台，静默死亡和线程卡住否则不会留下任何证据
import diagnostics
diagnostics.enable(LOG_FILE)

import jarvis
from edge_speech import PRESET_VOICES

# 状态通信队列
ui_queue = queue.Queue()

def on_hear(text):
    ui_queue.put(("hear", text))
    ui_queue.put(("status", "THINKING 思考中"))

def on_speak(text):
    ui_queue.put(("speak", text))
    ui_queue.put(("status", "SPEAKING 回复中"))

def on_speak_end():
    ui_queue.put(("status", "LISTENING 聆听中"))

def on_audio_level(level):
    ui_queue.put(("level", level))

jarvis.ON_HEAR = on_hear
jarvis.ON_SPEAK = on_speak
jarvis.ON_SPEAK_END = on_speak_end
jarvis.ON_AUDIO_LEVEL = on_audio_level

# 配色规范
CHROMA_KEY = "#02060d"
CYAN = "#00f7ff"
CYAN_CORE = "#80fbff"
CYAN_MID = "#00a2cc"
CYAN_DARK = "#004766"
CYAN_DEEP = "#002333"
BLUE = "#1a8cff"
GREEN = "#00ffa3"
WHITE = "#ffffff"
BG_PANEL = "#050e18"
BG_CARD = "#091726"
BORDER = "#103954"
BORDER_GLOW = "#00d0eb"
TEXT_MUTED = "#557e9c"

# =========================================================================
# 模块一：3D 反应堆核心引擎
# =========================================================================
class Reactor3DCore:
    def __init__(self, canvas, cx, cy, radius=80, fov=320.0):
        self.cv = canvas
        self.cx = cx
        self.cy = cy
        self.base_r = radius
        self.fov = fov
        self.rot_x = 0.38
        self.rot_y = 0.0
        self.rot_z = 0.0
        self.pulse = 0.0
        self._init_geometry()

        self.particles = []
        for _ in range(36):
            theta = random.uniform(0, math.tau)
            phi = random.uniform(-math.pi/2, math.pi/2)
            rad = random.uniform(radius * 0.9, radius * 1.55)
            self.particles.append({
                "r": rad, "theta": theta, "phi": phi,
                "spd": random.uniform(0.016, 0.038),
                "size": random.uniform(1.2, 2.5)
            })

    def _init_geometry(self):
        seg = 36
        r = self.base_r
        self.ring_eq = [(r * math.cos(i * math.tau / seg), 0, r * math.sin(i * math.tau / seg)) for i in range(seg)]
        self.ring_t1 = [
            (r * 0.92 * math.cos(i * math.tau / seg),
             r * 0.92 * math.sin(i * math.tau / seg) * math.sin(math.pi/4),
             r * 0.92 * math.sin(i * math.tau / seg) * math.cos(math.pi/4))
            for i in range(seg)
        ]
        self.ring_t2 = [
            (r * 0.84 * math.cos(i * math.tau / seg),
             r * 0.84 * math.sin(i * math.tau / seg) * math.sin(-math.pi/4),
             r * 0.84 * math.sin(i * math.tau / seg) * math.cos(-math.pi/4))
            for i in range(seg)
        ]
        phi = (1 + math.sqrt(5)) / 2
        scale = r * 0.32
        raw_v = [
            (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
            (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
            (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1)
        ]
        self.core_v = [(x * scale, y * scale, z * scale) for x, y, z in raw_v]
        self.core_edges = [
            (0, 11), (0, 5), (0, 1), (0, 7), (0, 10),
            (1, 5), (1, 9), (1, 8), (1, 7),
            (2, 11), (2, 4), (2, 3), (2, 6), (2, 10),
            (3, 4), (3, 9), (3, 8), (3, 6),
            (4, 5), (4, 9), (4, 11), (5, 9), (5, 11),
            (6, 7), (6, 8), (6, 10), (7, 8), (7, 10),
            (8, 9), (10, 11)
        ]

    def project(self, x, y, z, rx, ry, rz):
        y1 = y * math.cos(rx) - z * math.sin(rx)
        z1 = y * math.sin(rx) + z * math.cos(rx)
        x2 = x * math.cos(ry) + z1 * math.sin(ry)
        z2 = -x * math.sin(ry) + z1 * math.cos(ry)
        x3 = x2 * math.cos(rz) - y1 * math.sin(rz)
        y3 = x2 * math.sin(rz) + y1 * math.cos(rz)
        z3 = z2
        dist = max(1.0, self.fov + z3)
        scale = self.fov / dist
        return self.cx + x3 * scale, self.cy + y3 * scale, z3

    def render(self, t, audio_level=0.0):
        self.rot_y = t * 0.95
        self.rot_x = 0.38 + math.sin(t * 0.4) * 0.12
        self.rot_z = math.sin(t * 0.6) * 0.08
        self.pulse = t * 3.5

        queue = []
        for p in self.particles:
            p["theta"] += p["spd"]
            px = p["r"] * math.cos(p["phi"]) * math.cos(p["theta"])
            py = p["r"] * math.sin(p["phi"])
            pz = p["r"] * math.cos(p["phi"]) * math.sin(p["theta"])
            sx, sy, sz = self.project(px, py, pz, self.rot_x, self.rot_y, self.rot_z)
            queue.append({"type": "particle", "z": sz, "x": sx, "y": sy, "size": p["size"]})

        for ring_pts, r_col, r_spd, r_w in [
            (self.ring_eq, CYAN, 1.0, 2),
            (self.ring_t1, BLUE, 1.4, 2),
            (self.ring_t2, CYAN_MID, -0.9, 1),
        ]:
            proj_pts = [self.project(x, y, z, self.rot_x, self.rot_y * r_spd, self.rot_z) for x, y, z in ring_pts]
            for i in range(len(proj_pts)):
                p1, p2 = proj_pts[i], proj_pts[(i + 1) % len(proj_pts)]
                queue.append({
                    "type": "line", "z": (p1[2] + p2[2]) / 2.0,
                    "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1],
                    "color": r_col, "width": r_w
                })

        core_scale = 1.0 + math.sin(self.pulse) * 0.12 + audio_level * 0.35
        core_proj = [
            self.project(vx * core_scale, vy * core_scale, vz * core_scale,
                         -self.rot_x * 1.5, -self.rot_y * 1.8, self.rot_z * 2.0)
            for vx, vy, vz in self.core_v
        ]
        for (i1, i2) in self.core_edges:
            p1, p2 = core_proj[i1], core_proj[i2]
            queue.append({
                "type": "core_edge", "z": (p1[2] + p2[2]) / 2.0,
                "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]
            })

        csx, csy, csz = self.project(0, 0, 0, self.rot_x, self.rot_y, self.rot_z)
        queue.append({"type": "glow", "z": csz, "x": csx, "y": csy, "level": audio_level})

        queue.sort(key=lambda it: it["z"])
        for it in queue:
            z = it["z"]
            z_factor = max(0.2, min(1.0, (z + self.base_r * 1.5) / (self.base_r * 3.0)))
            if it["type"] == "line":
                col = it["color"] if z > 0 else CYAN_DEEP
                w = it["width"] if z > 0 else 1
                self.cv.create_line(it["x1"], it["y1"], it["x2"], it["y2"], fill=col, width=w)
            elif it["type"] == "core_edge":
                ecol = WHITE if z > 10 else (CYAN_CORE if z > -10 else CYAN_MID)
                self.cv.create_line(it["x1"], it["y1"], it["x2"], it["y2"], fill=ecol, width=2 if z > 0 else 1)
            elif it["type"] == "particle":
                pr = it["size"] * (0.8 + 0.6 * z_factor)
                pcol = WHITE if z > 20 else (CYAN if z > -15 else CYAN_DARK)
                self.cv.create_oval(it["x"] - pr, it["y"] - pr, it["x"] + pr, it["y"] + pr, fill=pcol, outline="")
            elif it["type"] == "glow":
                cr = self.base_r * 0.28 + math.sin(self.pulse) * 3 + it["level"] * 10
                self.cv.create_oval(it["x"] - cr * 1.5, it["y"] - cr * 1.5, it["x"] + cr * 1.5, it["y"] + cr * 1.5, fill="#04263b", outline="")
                self.cv.create_oval(it["x"] - cr, it["y"] - cr, it["x"] + cr, it["y"] + cr, fill="#007799", outline="")
                self.cv.create_oval(it["x"] - cr * 0.6, it["y"] - cr * 0.6, it["x"] + cr * 0.6, it["y"] + cr * 0.6, fill=CYAN, outline="")
                self.cv.create_oval(it["x"] - cr * 0.3, it["y"] - cr * 0.3, it["x"] + cr * 0.3, it["y"] + cr * 0.3, fill=WHITE, outline="")
                self.cv.create_text(it["x"], it["y"] - 1, text="J", fill="#01131c", font=("Consolas", max(9, int(self.base_r * 0.16)), "bold"))


# =========================================================================
# 模块二：桌面 3D 全息悬浮球 (Orb Window)
# =========================================================================
class FloatingOrbWindow:
    def __init__(self, manager):
        self.mgr = manager
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=CHROMA_KEY)
        self.win.attributes("-transparentcolor", CHROMA_KEY)

        self.size = 150
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.x = sw - self.size - 40
        self.y = sh - self.size - 90
        self.win.geometry(f"{self.size}x{self.size}+{self.x}+{self.y}")

        self.cv = tk.Canvas(self.win, width=self.size, height=self.size,
                           bg=CHROMA_KEY, highlightthickness=0, cursor="hand2")
        self.cv.pack(fill="both", expand=True)

        self.reactor = Reactor3DCore(self.cv, self.size // 2, self.size // 2, radius=48, fov=200.0)

        # 拖拽与双击切换全屏主面板
        self._drag_x = 0
        self._drag_y = 0
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<Double-Button-1>", lambda e: self.mgr.switch_to_fullscreen())
        self.cv.bind("<Button-3>", self._show_menu)

    def _on_press(self, e):
        self._drag_x = e.x_root - self.win.winfo_x()
        self._drag_y = e.y_root - self.win.winfo_y()

    def _on_drag(self, e):
        # 同样夹在屏幕内, 防止拖出可视区域后"消失"
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        nx = max(0, min(sw - self.size, e.x_root - self._drag_x))
        ny = max(0, min(sh - self.size, e.y_root - self._drag_y))
        self.win.geometry(f"+{nx}+{ny}")
        self.x, self.y = nx, ny

    def _show_menu(self, e):
        m = tk.Menu(self.win, tearoff=0, bg=BG_PANEL, fg=WHITE, activebackground=CYAN_MID)
        m.add_command(label="展开全屏战术大中枢", command=self.mgr.switch_to_fullscreen)
        m.add_separator()
        m.add_command(label="隐藏到托盘", command=self.mgr.hide_all)
        m.add_command(label="退出贾维斯", command=self.mgr.quit_app)
        m.post(e.x_root, e.y_root)

    def render(self, t, level):
        self.cv.delete("all")
        self.reactor.render(t, level)
        c = self.size // 2
        self.cv.create_oval(c - 54, c - 54, c + 54, c + 54, outline=BORDER, width=1)
        self.cv.create_oval(c - 3, self.size - 10, c + 3, self.size - 4, fill=GREEN if self.mgr.is_listening else CYAN, outline="")


# =========================================================================
# 模块三：真·全屏战术全息指挥中枢 (Fullscreen HUD Window)
# =========================================================================
class TkFullscreenHUDWindow:
    def __init__(self, manager):
        self.mgr = manager
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=BG_PANEL)

        # 硬件级物理分辨率检测 (解决 2.5K/4K Windows 缩放导致不满屏的问题)
        try:
            user32 = ctypes.windll.user32
            self.sw = user32.GetSystemMetrics(0)
            self.sh = user32.GetSystemMetrics(1)
        except Exception:
            self.sw = self.win.winfo_screenwidth()
            self.sh = self.win.winfo_screenheight()

        self.win.geometry(f"{self.sw}x{self.sh}+0+0")

        # 核心交互快捷键：按 Esc 先闭嘴、再收起回桌面悬浮球
        self.win.bind("<Escape>", lambda e: escape_to_orb(self.mgr))

        # 面板折叠状态
        self.left_visible = True
        self.right_visible = True

        self.voice_list = ["云希", "云扬", "晓晓", "晓伊", "云健"]
        self.current_voice_idx = 0
        cur_v = jarvis.CONFIG.get("tts_voice", "云希")
        if cur_v in self.voice_list:
            self.current_voice_idx = self.voice_list.index(cur_v)

        self.robot_enabled = bool(jarvis.CONFIG.get("robot_voice", 0))

        # 硬件状态数据
        self.cpu_pct = 0.0
        self.mem_pct = 0.0
        self.disk_pct = 0.0
        self.last_hw_time = 0

        self._build_ui()

    def _build_ui(self):
        # 1. 顶部战术状态顶栏
        top_bar = tk.Frame(self.win, bg=BG_PANEL, height=52)
        top_bar.pack(fill="x", padx=30, pady=(15, 0))

        # 左侧：折叠/展开左侧栏按钮 + 状态标
        self.toggle_left_btn = tk.Button(top_bar, text="◀ 收起左栏", command=self._toggle_left,
                                         bg=BG_CARD, fg=CYAN, activebackground=CYAN_MID, activeforeground=WHITE,
                                         relief="flat", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=3)
        self.toggle_left_btn.pack(side="left", padx=(0, 15))

        tk.Label(top_bar, text="◆ J.A.R.V.I.S. 战术全息指挥中枢", fg=CYAN, bg=BG_PANEL,
                 font=("Consolas", 14, "bold")).pack(side="left")
        tk.Label(top_bar, text="[ FULLSCREEN HUD PROTOCOL ACTIVE ]", fg=TEXT_MUTED, bg=BG_PANEL,
                 font=("Consolas", 10)).pack(side="left", padx=15)

        # 右侧：折叠/展开右栏按钮 + 操作提示与收起按钮
        btn_box = tk.Frame(top_bar, bg=BG_PANEL)
        btn_box.pack(side="right")

        self.toggle_right_btn = tk.Button(btn_box, text="收起右栏 ▶", command=self._toggle_right,
                                          bg=BG_CARD, fg=CYAN, activebackground=CYAN_MID, activeforeground=WHITE,
                                          relief="flat", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=3)
        self.toggle_right_btn.pack(side="left", padx=(0, 15))

        tk.Label(btn_box, text="按 [ESC] 退出全屏", fg=GREEN, bg=BG_PANEL,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 15))

        tk.Button(btn_box, text="🗗 收起为悬浮球", command=self.mgr.switch_to_orb,
                  bg=BG_CARD, fg=WHITE, activebackground=CYAN_MID, activeforeground=WHITE,
                  relief="flat", font=("Microsoft YaHei UI", 10), padx=12, pady=3).pack(side="left", padx=6)

        tk.Button(btn_box, text="✕", command=self.mgr.hide_all,
                  bg="#381b23", fg="#ff8da0", activebackground="#5c2030",
                  relief="flat", font=("Consolas", 11, "bold"), padx=10, pady=2).pack(side="left")

        # 装饰分隔线
        sep = tk.Canvas(self.win, height=2, bg=BG_PANEL, highlightthickness=0)
        sep.pack(fill="x", padx=30, pady=(8, 10))
        sep.create_line(0, 1, self.sw, 1, fill=BORDER, width=1)
        sep.create_line(30, 1, 260, 1, fill=CYAN, width=2)

        # 主内容三栏容器
        self.main_content = tk.Frame(self.win, bg=BG_PANEL)
        self.main_content.pack(fill="both", expand=True, padx=30, pady=(0, 20))

        # -----------------------------------------------------------------
        # 【左侧】：机械弧形/圆形任务罗盘区 (Task Dial Panel)
        # -----------------------------------------------------------------
        self.left_col = tk.Frame(self.main_content, bg=BG_PANEL, width=380)
        self.left_col.pack(side="left", fill="both", padx=(0, 15))
        self.left_col.pack_propagate(False)

        tk.Label(self.left_col, text="● 机械控制罗盘与快捷槽 (TASK DIAL)", fg=CYAN, bg=BG_PANEL,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

        self.dial_w = 380
        self.dial_h = 320
        self.dial_cv = tk.Canvas(self.left_col, width=self.dial_w, height=self.dial_h,
                                 bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER, cursor="hand2")
        self.dial_cv.pack(fill="x", pady=5)
        self.dial_cv.bind("<Button-1>", self._on_dial_click)

        # 快捷战术动作插槽
        tk.Label(self.left_col, text="战术动作插槽 (TACTICAL SLOTS)", fg=TEXT_MUTED, bg=BG_PANEL,
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(10, 4))

        slots_frame = tk.Frame(self.left_col, bg=BG_PANEL)
        slots_frame.pack(fill="x", pady=2)

        def make_slot_btn(parent, text, cmd_text):
            b = tk.Button(parent, text=text,
                          bg=BG_CARD, fg=WHITE, activebackground=CYAN_MID, activeforeground=WHITE,
                          relief="flat", font=("Microsoft YaHei UI", 9), padx=10, pady=6,
                          command=lambda: threading.Thread(target=jarvis.handle_command, args=(cmd_text,), daemon=True).start())
            b.pack(side="left", fill="x", expand=True, padx=3)
            return b

        r1 = tk.Frame(slots_frame, bg=BG_PANEL)
        r1.pack(fill="x", pady=2)
        make_slot_btn(r1, "📸 快速截图", "截图")
        make_slot_btn(r1, "🔒 立即锁屏", "锁屏")

        r2 = tk.Frame(slots_frame, bg=BG_PANEL)
        r2.pack(fill="x", pady=2)
        make_slot_btn(r2, "🌤 查询天气", "搜索明天天气")
        make_slot_btn(r2, "📝 启动记事本", "打开记事本")

        # -------------------------------------------------------------
        # 【左侧底部】：AI 大模型与 API KEY 快捷配置舱
        # -------------------------------------------------------------
        ai_cfg_frame = tk.Frame(self.left_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER)
        ai_cfg_frame.pack(fill="x", pady=(12, 0))

        tk.Label(ai_cfg_frame, text="◆ AI 大模型核心配置", fg=CYAN, bg=BG_CARD,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        # 模型服务商选择
        p_row = tk.Frame(ai_cfg_frame, bg=BG_CARD)
        p_row.pack(fill="x", padx=10, pady=2)
        tk.Label(p_row, text="服务商:", fg=TEXT_MUTED, bg=BG_CARD, font=("Microsoft YaHei UI", 8)).pack(side="left")

        from llm_brain import LLM_PROVIDERS
        self.provider_names = list(LLM_PROVIDERS.keys())
        self.provider_var = tk.StringVar(value=jarvis.CONFIG.get("ai_provider", "DeepSeek"))

        # 下拉选单
        p_opt = tk.OptionMenu(p_row, self.provider_var, *self.provider_names, command=self._on_provider_change)
        p_opt.config(bg="#0e2336", fg=WHITE, activebackground=CYAN_MID, activeforeground=WHITE,
                     highlightthickness=0, relief="flat", font=("Consolas", 8))
        p_opt["menu"].config(bg=BG_CARD, fg=WHITE, activebackground=CYAN_MID)
        p_opt.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # API KEY 输入框
        k_row = tk.Frame(ai_cfg_frame, bg=BG_CARD)
        k_row.pack(fill="x", padx=10, pady=(6, 8))
        tk.Label(k_row, text="API KEY:", fg=TEXT_MUTED, bg=BG_CARD, font=("Consolas", 8)).pack(side="left")

        current_key = jarvis.CONFIG.get("api_key", "")
        self.key_var = tk.StringVar(value=current_key)
        self.key_entry = tk.Entry(k_row, textvariable=self.key_var, show="*", bg="#05101a", fg=CYAN_CORE,
                                  insertbackground=CYAN, relief="flat", font=("Consolas", 9),
                                  highlightthickness=1, highlightbackground=BORDER)
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=2)

        save_key_btn = tk.Button(k_row, text="保存", command=self._save_api_key,
                                 bg="#0f3c59", fg=CYAN, activebackground=CYAN, activeforeground="#000",
                                 relief="flat", font=("Microsoft YaHei UI", 8, "bold"), padx=8)
        save_key_btn.pack(side="right")

        # -----------------------------------------------------------------
        # 【右侧】：硬件状态监控 + 实时日志流 (先放在中间之前声明，便于自由伸缩)
        # -----------------------------------------------------------------
        self.right_col = tk.Frame(self.main_content, bg=BG_PANEL, width=420)
        self.right_col.pack(side="right", fill="both", padx=(15, 0))
        self.right_col.pack_propagate(False)

        tk.Label(self.right_col, text="● 硬件监控与战术日志 (STATUS & LOGS)", fg=CYAN, bg=BG_PANEL,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

        self.hw_cv = tk.Canvas(self.right_col, height=140, bg=BG_CARD,
                               highlightthickness=1, highlightbackground=BORDER)
        self.hw_cv.pack(fill="x", pady=(0, 10))

        tk.Label(self.right_col, text="系统底层流水线记录 (EVENT STREAM)", fg=TEXT_MUTED, bg=BG_PANEL,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(5, 4))

        self.log_box = scrolledtext.ScrolledText(self.right_col, bg="#030b14", fg="#75a5c7",
                                                font=("Consolas", 9), relief="flat", bd=0,
                                                insertbackground=CYAN,
                                                highlightthickness=1, highlightbackground=BORDER)
        self.log_box.pack(fill="both", expand=True)

        # -----------------------------------------------------------------
        # 【中央】：超大 3D 反应堆 + 全息对话大屏 + 指令输入台 (自适应填满剩余空间)
        # -----------------------------------------------------------------
        self.mid_col = tk.Frame(self.main_content, bg=BG_PANEL)
        self.mid_col.pack(side="left", fill="both", expand=True, padx=15)

        # 1. 超大 3D 反应堆画布
        self.center_cv_h = int(self.sh * 0.44)
        self.center_cv = tk.Canvas(self.mid_col, height=self.center_cv_h,
                                   bg=BG_PANEL, highlightthickness=0)
        self.center_cv.pack(fill="x", pady=(0, 5))
        self.reactor = Reactor3DCore(self.center_cv, 400, self.center_cv_h // 2, radius=95, fov=340.0)

        # 2. 动态全息声波谱线
        self.wave_cv = tk.Canvas(self.mid_col, height=36, bg=BG_CARD,
                                 highlightthickness=1, highlightbackground=BORDER)
        self.wave_cv.pack(fill="x", pady=(0, 10))

        # 3. 贾维斯全息对话大屏 (去掉死板的“你说的”框)
        self.resp_frame = tk.Frame(self.mid_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER)
        self.resp_frame.pack(fill="both", expand=True, pady=(0, 12))

        tk.Label(self.resp_frame, text="◆ J.A.R.V.I.S. INTELLIGENCE OUTPUT", fg=CYAN, bg=BG_CARD,
                 font=("Consolas", 10, "bold")).pack(anchor="w", padx=20, pady=(12, 4))

        self.jarvis_text_var = tk.StringVar(value="全屏战术指挥大厅已就绪。Sir，随时听候您的差遣。")
        self.resp_label = tk.Label(self.resp_frame, textvariable=self.jarvis_text_var,
                                   fg=WHITE, bg=BG_CARD, font=("Microsoft YaHei UI", 13),
                                   wraplength=800, justify="left", anchor="nw",
                                   padx=20, pady=10)
        self.resp_label.pack(fill="both", expand=True)

        # 4. 底部键盘打字控制台
        input_container = tk.Frame(self.mid_col, bg=BG_CARD, highlightthickness=1, highlightbackground=BORDER_GLOW)
        input_container.pack(fill="x")

        tk.Label(input_container, text="▶ 指令:", fg=CYAN, bg=BG_CARD,
                 font=("Consolas", 11, "bold")).pack(side="left", padx=(12, 0))

        self.entry = tk.Entry(input_container, bg=BG_CARD, fg=WHITE, insertbackground=CYAN,
                              relief="flat", font=("Microsoft YaHei UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        self.entry.bind("<Return>", lambda e: self._send_cmd())

        tk.Button(input_container, text="发送指令", command=self._send_cmd,
                  bg="#0d4061", fg=CYAN, activebackground=CYAN, activeforeground="#000",
                  relief="flat", font=("Microsoft YaHei UI", 10, "bold"), padx=16, pady=4).pack(side="right", padx=5)

    def _toggle_left(self):
        """折叠 / 展开左侧控制盘"""
        if self.left_visible:
            self.left_col.pack_forget()
            self.toggle_left_btn.config(text="展开左栏 ▶")
            self.left_visible = False
        else:
            self.left_col.pack(side="left", fill="both", padx=(0, 15), before=self.mid_col)
            self.toggle_left_btn.config(text="◀ 收起左栏")
            self.left_visible = True

    def _toggle_right(self):
        """折叠 / 展开右侧状态日志栏"""
        if self.right_visible:
            self.right_col.pack_forget()
            self.toggle_right_btn.config(text="◀ 展开右栏")
            self.right_visible = False
        else:
            # 必须插回 mid_col 之前: pack 按顺序分配空间, mid_col 是 expand=True,
            # 若右栏排在它后面就只能分到 0 宽度 (表现成"展不开")
            self.right_col.pack(side="right", fill="both", padx=(15, 0),
                                before=self.mid_col)
            self.toggle_right_btn.config(text="收起右栏 ▶")
            self.right_visible = True

    def _on_provider_change(self, val):
        jarvis.CONFIG["ai_provider"] = val
        self._save_config("ai_provider", val)
        print(f"[AI 模型] 切换为 {val}")

    def _save_api_key(self):
        new_key = self.key_var.get().strip()
        from llm_brain import clean_api_key, key_hint
        if not new_key:
            # 留空 = "沿用已保存的那把", 不能用空串把它覆盖掉:
            # 以前这里的写回语句在 if 之外, 于是框里没填内容点一次保存就把 Key 清空了。
            hint = key_hint(jarvis.CONFIG.get("api_key"))
            self.jarvis_text_var.set(
                f"未输入新密钥，已保留原有配置：{hint}（服务商：{self.provider_var.get()}）。"
                "如需更换，请先粘贴新密钥再点保存。")
            print(f"[AI 密钥] 输入为空, 保留现有配置 ({hint})")
            return
        if not clean_api_key(new_key):
            # 常见情况: 粘进来一大段文字/带换行, 直接拒绝而不是写进配置
            self.jarvis_text_var.set(
                "密钥格式不对：应该是一串不含空格和换行的字符（以 sk- 开头那种）。请重新复制一次再保存。")
            print(f"[AI 密钥] 拒绝保存: 格式不合法 ({len(new_key)} 字符, 含空格或非 ASCII)")
            return
        jarvis.CONFIG["api_key"] = new_key
        self._save_config("api_key", new_key)
        # 同步写入环境
        os.environ["AI_API_KEY"] = new_key
        self.jarvis_text_var.set(
            f"AI 密钥已保存：{key_hint(new_key)}。当前接入服务商：{self.provider_var.get()}。随时向我提问吧，Sir。")
        print(f"[AI 密钥] 已写入用户配置 ({jarvis.USER_CFG_FILE})：{key_hint(new_key)}")

    def _on_dial_click(self, event):
        cx = self.dial_w // 2
        cy = self.dial_h // 2
        dx = event.x - cx
        dy = event.y - cy
        dist = math.hypot(dx, dy)

        # 点击中心切换机械音
        if dist < 42:
            self.robot_enabled = not self.robot_enabled
            jarvis.CONFIG["robot_voice"] = 0.7 if self.robot_enabled else 0
            self._save_config("robot_voice", 0.7 if self.robot_enabled else 0)
            return

        # 点击外环扇区切换声线
        if 55 < dist < 125:
            ang = math.atan2(dy, dx)
            ang_deg = (math.degrees(ang) + 90) % 360
            idx = int(ang_deg // (360 / 5)) % 5
            self.current_voice_idx = idx
            chosen_v = self.voice_list[idx]
            jarvis.CONFIG["tts_voice"] = chosen_v
            self._save_config("tts_voice", chosen_v)

    def _save_config(self, k, v):
        try:
            # 写到用户目录: 重装或重新打包都不会覆盖掉自己的设置
            cfg = jarvis.USER_CFG_FILE
            cfg.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
            data[k] = v
            cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[配置保存失败] {e}")

    def _send_cmd(self):
        cmd = self.entry.get().strip()
        if not cmd: return
        self.entry.delete(0, "end")
        threading.Thread(target=jarvis.handle_command, args=(cmd,), daemon=True).start()

    def render(self, t, level):
        now = time.time()
        if now - self.last_hw_time > 1.0:
            self.last_hw_time = now
            try:
                self.cpu_pct = psutil.cpu_percent()
                self.mem_pct = psutil.virtual_memory().percent
                self.disk_pct = psutil.disk_usage('C:').percent
            except Exception: pass

        # 1. 渲染左侧罗盘 (如可见)
        if self.left_visible:
            self._render_dial(t)

        # 2. 渲染中央 3D 反应堆 (自适应当前居中宽度)
        self.center_cv.delete("all")
        cw = self.center_cv.winfo_width()
        if cw > 100:
            self.reactor.cx = cw // 2
        self.reactor.cy = self.center_cv_h // 2
        self.reactor.render(t, level)

        # 动态自适应换行宽度
        resp_w = self.resp_frame.winfo_width()
        if resp_w > 100:
            self.resp_label.config(wraplength=resp_w - 40)

        # 3. 渲染中央声波谱线
        self.wave_cv.delete("all")
        ww = self.wave_cv.winfo_width() or 600
        bars = 52
        gw = ww / bars
        mid = 18
        for i in range(bars):
            mid_dist = abs(i - bars / 2) / (bars / 2)
            wave_h = (math.sin(t * 8 + i * 0.45) * 0.5 + 0.5) * level * 26 * (1.15 - mid_dist) + 2
            bx = (i + 0.5) * gw
            col = CYAN if level > 0.12 else CYAN_MID
            self.wave_cv.create_line(bx, mid - wave_h/2, bx, mid + wave_h/2, fill=col, width=2)

        # 4. 渲染右侧硬件监控 (如可见)
        if self.right_visible:
            self._render_hw_monitor()

    def _render_dial(self, t):
        cv = self.dial_cv
        cv.delete("all")
        cx = self.dial_w // 2
        cy = self.dial_h // 2

        cv.create_oval(cx - 130, cy - 130, cx + 130, cy + 130, outline=BORDER, width=1)
        for i in range(36):
            a = i * math.tau / 36 + t * 0.15
            r1 = 126 if i % 3 == 0 else 129
            r2 = 132
            cv.create_line(cx + math.cos(a)*r1, cy + math.sin(a)*r1,
                           cx + math.cos(a)*r2, cy + math.sin(a)*r2,
                           fill=CYAN if i % 3 == 0 else BORDER, width=1)

        sector_r = 92
        for i, name in enumerate(self.voice_list):
            sa = (i * (math.tau / 5)) - math.pi/2
            px = cx + math.cos(sa) * sector_r
            py = cy + math.sin(sa) * sector_r
            is_active = (i == self.current_voice_idx)

            node_r = 18 if is_active else 13
            node_fill = "#0f3e5c" if is_active else BG_CARD
            node_outline = CYAN if is_active else BORDER
            cv.create_oval(px - node_r, py - node_r, px + node_r, py + node_r,
                           fill=node_fill, outline=node_outline, width=2 if is_active else 1)

            cv.create_text(px, py, text=name, fill=WHITE if is_active else TEXT_MUTED,
                           font=("Microsoft YaHei UI", 9, "bold" if is_active else "normal"))

        center_r = 38
        robot_on = self.robot_enabled
        core_fill = "#0a3c59" if robot_on else "#121d28"
        core_outline = CYAN if robot_on else BORDER
        cv.create_oval(cx - center_r, cy - center_r, cx + center_r, cy + center_r,
                       fill=core_fill, outline=core_outline, width=2)
        cv.create_text(cx, cy - 7, text="机械音", fill=CYAN if robot_on else TEXT_MUTED,
                       font=("Microsoft YaHei UI", 9, "bold"))
        cv.create_text(cx, cy + 10, text="[ON]" if robot_on else "[OFF]",
                       fill=GREEN if robot_on else "#5c2b36", font=("Consolas", 8, "bold"))

        cv.create_text(cx, self.dial_h - 14, text="点击外环换声线 · 点击中心切换机械音",
                       fill=TEXT_MUTED, font=("Microsoft YaHei UI", 8))

    def _render_hw_monitor(self):
        cv = self.hw_cv
        cv.delete("all")
        w = cv.winfo_width() or 420
        padding = 24
        bar_w = w - padding * 2

        items = [
            ("CPU 负载", self.cpu_pct, CYAN),
            ("内存使用", self.mem_pct, BLUE),
            ("主盘空间", self.disk_pct, GREEN)
        ]

        y_base = 22
        for title, pct, color in items:
            cv.create_text(padding, y_base, text=title, anchor="w", fill=TEXT_MUTED, font=("Microsoft YaHei UI", 9))
            cv.create_text(w - padding, y_base, text=f"{pct:.1f}%", anchor="e", fill=color, font=("Consolas", 10, "bold"))

            bar_y = y_base + 12
            cv.create_rectangle(padding, bar_y, padding + bar_w, bar_y + 4, fill="#040d16", outline="")
            fill_len = max(2, bar_w * (pct / 100.0))
            cv.create_rectangle(padding, bar_y, padding + fill_len, bar_y + 4, fill=color, outline="")

            y_base += 40


# 默认全屏主界面改为 PyWebView；上面的 TkFullscreenHUDWindow 保留作历史参考。
from web_fullscreen import FullscreenHUDWindow


# =========================================================================
# 模块四：主控总协调器 (Manager)
# =========================================================================
class JarvisAppManager:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.mode = "orb"
        self.is_listening = True
        self.audio_level = 0.0
        self.target_audio_level = 0.0
        self.time_start = time.time()

        self.orb_win = FloatingOrbWindow(self)
        self.hud_win = FullscreenHUDWindow(self)

        # ── 界面层: Web 胶囊 (默认) / Tk 胶囊 / 经典 ──
        self.ui_style = UI_STYLE
        self.capsule = None
        self.overlay = None
        self.web_capsule = None
        self.lyric_subtitle = None

        # 全屏穿透层两种胶囊界面都要用
        if self.ui_style in ("web", "new"):
            try:
                from hud_overlay import HudOverlay
                self.overlay = HudOverlay(self.root, self)
            except Exception as _e:
                print(f"[穿透层启动失败] {_e}")

        # Web 胶囊 (PyWebView 子进程)
        if self.ui_style == "web":
            try:
                from web_capsule import WebCapsuleHost
                self.web_capsule = WebCapsuleHost(
                    on_classic=lambda: self.root.after(0, self.use_classic_ui))
                self.web_capsule.start()
                self.mode = "web"
                print("[界面] Web 胶囊已启动 (PyWebView + HTML/CSS)")
            except Exception as _e:
                print(f"[Web 胶囊启动失败, 退回 Tk 胶囊] {_e}")
                self.web_capsule = None
                self.ui_style = "new"

        # Tk 胶囊 (备用)
        if self.ui_style == "new":
            try:
                from capsule import CapsuleWindow
                from hud_overlay import HudOverlay
                if self.overlay is None:
                    self.overlay = HudOverlay(self.root, self)
                self.capsule = CapsuleWindow(self, self.overlay)
                self.mode = "capsule"
                print(f"[界面] Tk 胶囊已启用 屏幕={self.capsule.SW}x{self.capsule.SH} "
                      f"位置=({self.capsule.x},{self.capsule.y})")
            except Exception as _e:
                print(f"[胶囊界面启动失败, 退回经典界面] {_e}")
                traceback.print_exc()
                self.ui_style = "classic"
                self.capsule = None

        # 全屏 PyWebView 按需启动，初始只显示灵动岛/悬浮球。
        if self.mode == "web":
            self.orb_win.win.withdraw()
        elif self.ui_style == "new" and self.capsule is not None:
            self.orb_win.win.withdraw()
            self.overlay.show()
            self.capsule.show()
            self.capsule.win.lift()
        else:
            self.orb_win.win.deiconify()

        # 桌面歌词式回复字幕：只显示最终回复，全屏时由网页内字幕接管。
        try:
            from lyric_subtitle import LyricSubtitleWindow
            self.lyric_subtitle = LyricSubtitleWindow(self.root)
            self.lyric_subtitle.set_enabled(True)
        except Exception as _e:
            print(f"[歌词字幕启动失败] {_e}")

        self.tray_icon = None
        self._start_tray()

        threading.Thread(target=self._start_core, daemon=True).start()

        # 界面演示模式: 不用说话也能看六种状态 (JARVIS_UI_DEMO=1)
        if os.environ.get("JARVIS_UI_DEMO") == "1":
            threading.Thread(target=self._demo_states, daemon=True).start()

        self._render_tick()
        self._poll_ui()
        self._start_watchdog()

    def _watchdog_problems(self):
        """采集"应该干活却没动静"的信号；由 diagnostics 看门狗定期调用。"""
        problems = []
        # 1) Tk 主循环心跳（after 链断了/主线程被阻塞都会停跳）
        gap = diagnostics.since("tk")
        if gap > 5.0:
            problems.append(f"Tk 主循环已 {gap:.0f}s 没有心跳")

        # 2) whisper 常驻监听心跳（"界面在聆听、其实听不到"就是这种）
        try:
            import whisper_engine
            hb = whisper_engine.last_heartbeat()
            if hb.get("state") == "fatal":
                problems.append(f"whisper 已停止: {hb.get('last_error')}")
            elif hb.get("state") == "listening":
                idle = time.time() - float(hb.get("last_read") or 0)
                if idle > 8.0:
                    problems.append(f"whisper 已 {idle:.0f}s 没有读到音频块")
        except Exception:
            pass

        # 3) Web 胶囊子进程
        if self.mode == "web" and self.web_capsule is not None:
            if not getattr(self.web_capsule, "alive", True):
                problems.append("Web 胶囊子进程已断开")
        return problems

    def _start_watchdog(self):
        diagnostics.start_watchdog(self._watchdog_problems, LOG_FILE)
        diagnostics.mark("tk")

    @staticmethod
    def _demo_states():
        """循环推六个状态, 方便预览视觉 (仅演示用)"""
        seq = [
            ("LISTENING", 0.55, "贾维斯, 帮我打开微信"),
            ("THINKING", 0.08, "正在理解你的指令"),
            ("ACTING", 0.30, "已打开微信"),
            ("PERCEIVING", 0.05, "正在扫描当前窗口"),
            ("GUARD", 0.02, "你说的是关机, 确定要执行吗"),
            ("IDLE", 0.02, "已取消"),
        ]
        time.sleep(4)
        while True:
            for st, lv, sub in seq:
                ui_bridge.emit_state(st)
                ui_bridge.emit_audio_level(lv)
                ui_bridge.emit_subtitle(sub, st in ("ACTING", "IDLE"))
                time.sleep(3.2)

    def switch_to_fullscreen(self):
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(False)
        self._hide_new_ui()
        self.orb_win.win.withdraw()
        try:
            self.hud_win.show()
            self.mode = "fullscreen"
        except Exception as exc:
            print(f"[全屏 HUD 启动失败] {exc}")
            self.use_web_ui()

    def switch_to_orb(self):
        """收起全屏的对外入口：任何异常都必须落到兜底，不能停在半路。"""
        try:
            self._switch_to_orb()
        except Exception as exc:
            print(f"[界面] 收起界面失败: {exc}", flush=True)
            self._fallback_to_orb()

    def _switch_to_orb(self):
        """收起全屏：默认回到共用 ui_bridge 的 Web 灵动岛。

        不变量：调用结束时屏幕上必须有一个看得见、点得动的界面。
        以前这里的判断只覆盖"对象是否存在"，一旦胶囊子进程已经死了，
        就会走隐藏全屏 → 隐藏悬浮球 → 往死进程发显示指令的路径，
        结果全屏没了、兜底也没了，屏幕上什么都不剩 —— 用户看到的就是"退出"。
        """
        self.hud_win.hide()
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(True)

        if self.ui_style == "web" and self._ensure_web_capsule():
            self.orb_win.win.withdraw()
            if self.overlay is not None and not self.overlay._visible:
                self.overlay.show()
            if self.web_capsule.set_visible(True):
                self.mode = "web"
                return
            print("[界面] 胶囊显示指令没送达，回退悬浮球", flush=True)

        self._fallback_to_orb()

    def _fallback_to_orb(self):
        """最后一道兜底：无论上面哪一步失败，都必须留一个可交互的界面。"""
        self.mode = "orb"
        try:
            self.orb_win.win.deiconify()
            self.orb_win.win.lift()
        except Exception as exc:
            print(f"[界面] 悬浮球显示失败: {exc}", flush=True)

    def hide_all(self):
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(False)
        self.orb_win.win.withdraw()
        self.hud_win.hide()
        if self.overlay is not None:
            self.overlay.hide()
        if self.capsule is not None:
            self.capsule.hide()
        if self.web_capsule is not None:
            self.web_capsule.set_visible(False)

    def toggle_lyric_visibility(self):
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.toggle_visibility()

    def toggle_lyric_click_through(self):
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.toggle_click_through()

    def reset_lyric_position(self):
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.reset_position()

    # ── 界面风格切换 ──
    def _ensure_web_capsule(self):
        # 只判断"对象是否存在"远不够：子进程死了、连接断了的时候它仍然非空，
        # set_visible 会静默失败，于是调用方以为已经切到胶囊，屏幕上却什么都没有。
        if self.web_capsule is not None and getattr(self.web_capsule, "alive", False):
            return True
        if self.web_capsule is not None:
            print("[Web 胶囊] 旧实例已失效，重建", flush=True)
            try:
                self.web_capsule.stop()
            except Exception:
                pass
            self.web_capsule = None
        try:
            from web_capsule import WebCapsuleHost
            self.web_capsule = WebCapsuleHost(
                on_classic=lambda: self.root.after(0, self.use_classic_ui))
            self.web_capsule.start()
            return True
        except Exception as e:
            print(f"[Web 胶囊创建失败] {e}")
            self.web_capsule = None
            return False

    def _ensure_capsule(self):
        if self.capsule is not None:
            return True
        try:
            from capsule import CapsuleWindow
            from hud_overlay import HudOverlay
            if self.overlay is None:
                self.overlay = HudOverlay(self.root, self)
            self.capsule = CapsuleWindow(self, self.overlay)
            self.ui_style = "new"
            return True
        except Exception as e:
            print(f"[胶囊界面创建失败] {e}")
            return False

    def _hide_new_ui(self):
        if self.overlay is not None:
            self.overlay.hide()
        if self.capsule is not None:
            self.capsule.hide()
        if self.web_capsule is not None:
            self.web_capsule.set_visible(False)

    def use_web_ui(self):
        """Web 胶囊 + 穿透层 (默认界面)"""
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(True)
        if not self._ensure_web_capsule():
            return
        if self.capsule is not None:
            self.capsule.hide()
        self.orb_win.win.withdraw()
        self.hud_win.hide()
        if self.overlay is not None and not self.overlay._visible:
            self.overlay.show()
        self.web_capsule.set_visible(True)
        self.mode = "web"

    def use_capsule_ui(self):
        """Tk 胶囊 + 穿透层 (备用新界面)"""
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(True)
        if not self._ensure_capsule():
            return
        if self.web_capsule is not None:
            self.web_capsule.set_visible(False)
        self.orb_win.win.withdraw()
        self.hud_win.hide()
        self.overlay.show()
        self.capsule.show()
        self.capsule.win.lift()
        self.mode = "capsule"

    def use_classic_ui(self):
        """展开 PyWebView 全屏战术大中枢。"""
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(False)
        self._hide_new_ui()
        self.orb_win.win.withdraw()
        try:
            self.hud_win.show()
            self.mode = "fullscreen"
        except Exception as exc:
            print(f"[全屏 HUD 启动失败] {exc}")
            self.use_web_ui()

    def use_orb_ui(self):
        """经典桌面悬浮球"""
        if self.lyric_subtitle is not None:
            self.lyric_subtitle.set_enabled(True)
        self.hud_win.hide()
        self._hide_new_ui()
        self.mode = "orb"
        self.orb_win.win.deiconify()
        self.orb_win.win.lift()

    def quit_app(self):
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception: pass
        try:
            if self.hud_win:
                self.hud_win.stop()
        except Exception: pass
        try:
            if self.lyric_subtitle:
                self.lyric_subtitle.destroy()
        except Exception: pass
        try:
            if self.web_capsule:
                self.web_capsule.stop()
        except Exception: pass
        os._exit(0)

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), (5, 14, 24))
            d = ImageDraw.Draw(img)
            d.ellipse((8, 8, 56, 56), fill=(0, 247, 255))
            d.text((27, 22), "J", fill=(3, 15, 25))
            menu = pystray.Menu(
                pystray.MenuItem("显示 Web 胶囊", lambda _i, _it: self.root.after(0, self.use_web_ui), default=True),
                pystray.MenuItem("显示 Tk 胶囊", lambda _i, _it: self.root.after(0, self.use_capsule_ui)),
                pystray.MenuItem("展开全屏大中枢", lambda _i, _it: self.root.after(0, self.use_classic_ui)),
                pystray.MenuItem("切换为桌面悬浮球", lambda _i, _it: self.root.after(0, self.use_orb_ui)),
                pystray.MenuItem("显示 / 隐藏歌词字幕", lambda _i, _it: self.root.after(0, self.toggle_lyric_visibility)),
                pystray.MenuItem("切换字幕鼠标穿透", lambda _i, _it: self.root.after(0, self.toggle_lyric_click_through)),
                pystray.MenuItem("字幕恢复顶部居中", lambda _i, _it: self.root.after(0, self.reset_lyric_position)),
                pystray.MenuItem("隐藏界面", lambda _i, _it: self.root.after(0, self.hide_all)),
                pystray.MenuItem("退出贾维斯", lambda _i, _it: self.quit_app()),
            )
            self.tray_icon = pystray.Icon("jarvis", img, "贾维斯全屏全息系统", menu)
            self.tray_icon.run_detached()
        except Exception as e:
            print(f"[托盘启动失败] {e}")

    def _start_core(self):
        # 先让全屏/灵动岛 WebView 完成首帧，再加载 Whisper 模型与应用索引，避免启动瞬间 CPU 峰值叠加。
        time.sleep(1.5)
        try:
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        try:
            jarvis.wake_loop()
        except SystemExit: pass
        except Exception:
            traceback.print_exc()

    def _render_tick(self):
        diagnostics.mark("tk")
        try:
            t = time.time() - self.time_start
            self.audio_level += (self.target_audio_level - self.audio_level) * 0.35

            if self.mode == "web":
                if self.overlay is not None:
                    self.overlay.render()
            elif self.mode == "capsule" and self.capsule is not None:
                self.capsule.render(t, self.audio_level)
                if self.overlay is not None:
                    self.overlay.render()
            elif self.mode == "orb":
                self.orb_win.render(t, self.audio_level)
            elif self.mode == "fullscreen":
                self.hud_win.render(t, self.audio_level)
        except Exception:
            # 渲染出错只记录, 不让它冒泡
            traceback.print_exc()
        finally:
            # after 链必须无条件续上：一次渲染异常绝不能把整个界面永久冻住
            interval = 50 if self.mode in ("web", "fullscreen") else 30
            try:
                self.root.after(interval, self._render_tick)
            except Exception:
                pass

    def _poll_ui(self):
        # ── 状态总线 → 穿透层 / 歌词字幕 / Tk 胶囊 (Web 胶囊有自己的推送线程) ──
        try:
            snap = ui_bridge.snapshot()
            if self.overlay is not None and self.mode in ("capsule", "web"):
                self.overlay.apply_snapshot(snap)
                if self.capsule is not None:
                    last = getattr(self, "_last_snap_seq", -1)
                    if snap["seq"] != last or self.capsule._dirty:
                        self._last_snap_seq = snap["seq"]
                        self.capsule._dirty = False
                        self.capsule.apply_snapshot(snap)
            if self.lyric_subtitle is not None:
                self.lyric_subtitle.apply_snapshot(
                    snap, speaking=bool(jarvis.is_speaking()))
        except Exception:
            traceback.print_exc()

        try:
            while True:
                kind, val = ui_queue.get_nowait()
                if kind == "speak":
                    self.hud_win.set_output(val)
                elif kind == "level":
                    self.target_audio_level = max(0.0, min(1.0, float(val)))
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()

        # Web 胶囊子进程掉线 → 自动重启。
        # 不重启的话界面会直接消失，用户眼前就是"卡死"；起不来就退回经典界面保底。
        try:
            wc = self.web_capsule
            if self.mode == "web" and wc is not None and not getattr(wc, "alive", True):
                now = time.time()
                if now - getattr(self, "_wc_restart_at", 0.0) > 8.0:
                    self._wc_restart_at = now
                    print("[界面] Web 胶囊已断开，正在重启…")
                    try:
                        wc.stop()
                    except Exception:
                        pass
                    self.web_capsule = None
                    if self._ensure_web_capsule():
                        print("[界面] Web 胶囊已恢复")
                    else:
                        print("[界面] 胶囊重启失败，回退到经典界面")
                        self.use_classic_ui()
        except Exception:
            traceback.print_exc()

        # 日志队列只取有限条：界面一旦卡顿，把积压一次性倒进 Tk 会让它卡得更久
        try:
            for _ in range(200):
                try:
                    s = log_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.hud_win.append_log(s)
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()
        finally:
            # 同上：任何异常都不能断掉轮询链，否则 ui_queue 再没人消费，界面就假死
            try:
                self.root.after(100, self._poll_ui)
            except Exception:
                pass

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = JarvisAppManager()
    app.run()
