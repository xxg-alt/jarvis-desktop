# -*- coding: utf-8 -*-
"""
贾维斯 方案A：钢铁侠 HUD 悬浮反应堆 (Arc Reactor Floating Orb)
- 完全去除系统白色标题栏与死板大框
- 纯净桌面镂空透明悬浮，支持鼠标按住任意处自由拖拽
- 反应堆三环差速陀螺仪逆旋 + 呼吸发光能量核心
- 点击或说话时平滑展开全息光翼翅膀（展示声波与字幕），不说话时自动收拢为极简反应堆
- 包含微缩控制舱（展开/收拢、设置、托盘、退出）
"""
import json
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHROMA_KEY = "#030811"  # 镂空色键 (与系统透明融合)

# 颜色定义
BG_DARK = "#09121d"
PANEL_BG = "#0d1b2a"
CYAN = "#00f0ff"
CYAN_GLOW = "#1ae8ff"
BLUE = "#1e90ff"
GREEN = "#00ffaa"
WHITE = "#eaf6ff"
MUTED = "#52758e"
BORDER = "#14374f"

class FloatingArcReactor:
    def __init__(self, is_preview=False):
        self.is_preview = is_preview
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        
        # Windows 专属魔法：将特定背景色设为真透明通道，其余部分完全不透明清晰渲染！
        self.root.config(bg=CHROMA_KEY)
        self.root.attributes("-transparentcolor", CHROMA_KEY)

        # 尺寸与位置：增加宽度防止右侧裁切
        self.canvas_w = 560
        self.canvas_h = 260
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        init_x = sw - self.canvas_w - 40
        init_y = sh - self.canvas_h - 100
        self.root.geometry(f"{self.canvas_w}x{self.canvas_h}+{init_x}+{init_y}")

        self.cv = tk.Canvas(
            self.root, width=self.canvas_w, height=self.canvas_h,
            bg=CHROMA_KEY, highlightthickness=0, cursor="hand2"
        )
        self.cv.pack(fill="both", expand=True)

        # 状态变量
        self.orb_cx = 100
        self.orb_cy = 130
        self.expanded = True  # 是否展开全息翼板 (默认展开展示)
        self.expand_progress = 1.0  # 展开动画进度 0.0 ~ 1.0
        self.mode = "speaking" if is_preview else "idle"  # idle / listening / thinking / speaking
        
        self.phase = 0.0
        self.pulse = 0.0
        self.audio_level = 0.35 if is_preview else 0.0
        self.target_audio_level = 0.0

        self.user_text = "贾维斯，启动全息反应堆协议" if is_preview else "等待指令…"
        self.jarvis_text = "反应堆已在线，全息交互界面就绪。Sir，随时听候您的差遣。" if is_preview else "随时待命"
        self.status_text = "ONLINE" if is_preview else "STANDBY"

        # 粒子系统
        self.particles = []
        for _ in range(16):
            self.particles.append({
                "ang": random.uniform(0, math.tau),
                "dist": random.uniform(35, 75),
                "spd": random.uniform(0.02, 0.05),
                "rad": random.uniform(1, 2.2),
                "alpha": random.uniform(0.3, 0.9)
            })

        # 鼠标拖拽支持
        self._drag_start_x = 0
        self._drag_start_y = 0
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)

        # 双击反应堆切换展开/收起
        self.cv.bind("<Double-Button-1>", self._toggle_expand)

        self._tick()

    def _on_press(self, event):
        self._drag_start_x = event.x_root - self.root.winfo_x()
        self._drag_start_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def _toggle_expand(self, _event=None):
        self.expanded = not self.expanded

    def set_texts(self, hear=None, speak=None, status=None):
        if hear is not None:
            self.user_text = hear
            self.expanded = True  # 听到说话自动展开
        if speak is not None:
            self.jarvis_text = speak
            self.expanded = True
        if status is not None:
            self.status_text = status

    def _tick(self):
        self.phase += 0.038
        self.pulse += 0.06
        self.cv.delete("all")

        # 展开/折叠平滑过渡
        target_exp = 1.0 if self.expanded else 0.0
        self.expand_progress += (target_exp - self.expand_progress) * 0.18

        # 音量平滑
        if self.is_preview:
            t = time.time()
            self.audio_level = (math.sin(t * 5.0) + 1) * 0.4 + 0.1
        else:
            self.audio_level += (self.target_audio_level - self.audio_level) * 0.3

        cx, cy = self.orb_cx, self.orb_cy

        # ━━━━━━━━ 1. 全息翅膀展开背板 (HUD Holographic Wings) ━━━━━━━━
        ep = self.expand_progress
        if ep > 0.02:
            panel_x = cx + 55
            panel_w = int(370 * ep)
            panel_y1 = cy - 95
            panel_y2 = cy + 95

            # 科技感多边形切角卡片
            pts = [
                panel_x, panel_y1 + 14,
                panel_x + 14, panel_y1,
                panel_x + panel_w - 14, panel_y1,
                panel_x + panel_w, panel_y1 + 14,
                panel_x + panel_w, panel_y2 - 14,
                panel_x + panel_w - 14, panel_y2,
                panel_x + 14, panel_y2,
                panel_x, panel_y2 - 14,
            ]
            # 磨砂发光暗底 (纯色完全不透明，消除底层穿透脏乱感)
            self.cv.create_polygon(pts, fill=BG_DARK, outline=BORDER, width=1)
            # 上下高亮青色刻度线条
            self.cv.create_line(panel_x + 15, panel_y1, panel_x + min(panel_w, 90), panel_y1, fill=CYAN, width=2)
            self.cv.create_line(panel_x + panel_w - 50, panel_y2, panel_x + panel_w, panel_y2, fill=CYAN, width=2)

            # 只有展开过半时渲染文字
            if ep > 0.45:
                # 顶部状态与迷你指示
                self.cv.create_text(
                    panel_x + 20, panel_y1 + 18,
                    text="● J.A.R.V.I.S. HUD", anchor="w",
                    fill=CYAN, font=("Consolas", 10, "bold")
                )
                self.cv.create_text(
                    panel_x + panel_w - 20, panel_y1 + 18,
                    text=f"[ {self.status_text} ]", anchor="e",
                    fill=GREEN, font=("Consolas", 9, "bold")
                )

                # 动态频谱波形条 (说话时跃动)
                wave_y = panel_y1 + 42
                bar_count = 24
                gw = (panel_w - 40) / bar_count
                for i in range(bar_count):
                    # 模拟声波跳跃
                    mid_dist = abs(i - bar_count / 2) / (bar_count / 2)
                    wave_h = (math.sin(self.phase * 4 + i * 0.5) * 0.5 + 0.5) * self.audio_level * 18 * (1.1 - mid_dist) + 2
                    bx = panel_x + 20 + i * gw
                    b_color = CYAN if self.audio_level > 0.15 else MUTED
                    self.cv.create_line(bx, wave_y - wave_h/2, bx, wave_y + wave_h/2, fill=b_color, width=2)

                # 用户说的话
                u_text = self.user_text
                self.cv.create_text(
                    panel_x + 20, panel_y1 + 68,
                    text=f"USER ▸ {u_text}", anchor="w", width=panel_w - 40,
                    fill=WHITE, font=("Microsoft YaHei UI", 9)
                )

                # 贾维斯回答
                j_text = self.jarvis_text
                self.cv.create_text(
                    panel_x + 20, panel_y1 + 102,
                    text=j_text, anchor="nw", width=panel_w - 40,
                    fill=CYAN_GLOW, font=("Microsoft YaHei UI", 9)
                )

                # 底部科技辅助文字 (双击提示)
                self.cv.create_text(
                    panel_x + 20, panel_y2 - 14,
                    text="双击球体折叠 · 拖动任意处移动", anchor="w",
                    fill=MUTED, font=("Microsoft YaHei UI", 7)
                )

        # ━━━━━━━━ 2. 反应堆陀螺仪微型旋转环 (Arc Reactor Rings) ━━━━━━━━
        # 外发光辉光晕 (多层低透明度模拟发光)
        breath = math.sin(self.pulse) * 2.5
        reactor_r = 44 + breath

        # 粒子环绕
        for p in self.particles:
            p["ang"] += p["spd"]
            px = cx + math.cos(p["ang"]) * p["dist"]
            py = cy + math.sin(p["ang"]) * p["dist"]
            pr = p["rad"]
            self.cv.create_oval(px - pr, py - pr, px + pr, py + pr, fill=CYAN, outline="")

        # 最外层刻度瞄准线与三角瞄准标记
        self.cv.create_oval(cx - 58, cy - 58, cx + 58, cy + 58, outline=BORDER, width=1)
        for i in range(12):
            ang = i * (math.tau / 12) + self.phase * 0.2
            x1 = cx + math.cos(ang) * 56
            y1 = cy + math.sin(ang) * 56
            x2 = cx + math.cos(ang) * 62
            y2 = cy + math.sin(ang) * 62
            self.cv.create_line(x1, y1, x2, y2, fill=CYAN if i % 3 == 0 else BORDER, width=1)

        # 陀螺仪环 1: 正向高速旋转细齿环 (3段弧)
        for idx in range(3):
            st = math.degrees(self.phase * 1.8) + idx * 120
            self.cv.create_arc(
                cx - 48, cy - 48, cx + 48, cy + 48,
                start=st, extent=70, style="arc",
                outline=CYAN_GLOW, width=2
            )

        # 陀螺仪环 2: 逆向中速旋转环 (4段弧带点)
        for idx in range(4):
            st = math.degrees(-self.phase * 1.2) + idx * 90
            self.cv.create_arc(
                cx - 38, cy - 38, cx + 38, cy + 38,
                start=st, extent=42, style="arc",
                outline=BLUE, width=2
            )

        # 反应堆内圈发光环
        self.cv.create_oval(
            cx - 28, cy - 28, cx + 28, cy + 28,
            outline=CYAN, width=2, fill="#041829"
        )

        # 反应堆核心：超亮聚能核心 (发光层次)
        core_glow = 18 + breath * 0.8
        self.cv.create_oval(cx - core_glow, cy - core_glow, cx + core_glow, cy + core_glow, fill="#083854", outline="")
        self.cv.create_oval(cx - 12, cy - 12, cx + 12, cy + 12, fill="#00d8ff", outline="")
        self.cv.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="#ffffff", outline="")

        # 中心“J”全息微雕 (科技字样)
        self.cv.create_text(
            cx, cy - 1, text="J",
            fill="#031826", font=("Consolas", 10, "bold")
        )

        # 35ms 持续刷新动画 (~30fps，低功耗)
        self.root.after(35, self._tick)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = FloatingArcReactor(is_preview=True)
    app.run()
