# -*- coding: utf-8 -*-
"""
贾维斯 方案A 升级版：3D 高清全息陀螺仪反应堆 (3D Holographic Gyroscope Reactor)
- 纯数学 3D 透视投影引擎 (Pitch/Yaw/Roll 动态旋转)
- 3D 陀螺仪多重三维经纬轨道 + 3D 环绕全息浮游粒子云
- 反应堆内部立体晶体核心 (3D Icosahedron / 立体二十面体发光核)
- 景深透视衰减 (Z-buffer 颜色渐变 + 点线近大远小)
- 全息网格翼片 + 实时音频波形响应
"""
import math
import random
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
import numpy as np

BASE = Path(__file__).resolve().parent
CHROMA_KEY = "#02060d"  # 镂空色键 (全透明通道)

# 全息科技发光色系
CYAN_BRIGHT = "#00f7ff"
CYAN_CORE = "#80fbff"
CYAN_MID = "#00a2cc"
CYAN_DARK = "#004766"
CYAN_DEEP = "#002333"

BLUE_BRIGHT = "#1a8cff"
GREEN_GLOW = "#00ffa3"
WHITE_HOT = "#ffffff"

BG_PANEL = "#07131f"
BORDER_CYBER = "#0f3a54"
TEXT_DIM = "#5a829e"

class Hologram3DReactor:
    def __init__(self, is_preview=False):
        self.is_preview = is_preview
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        
        # 镂空纯净透明
        self.root.config(bg=CHROMA_KEY)
        self.root.attributes("-transparentcolor", CHROMA_KEY)

        self.canvas_w = 640
        self.canvas_h = 300
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        init_x = sw - self.canvas_w - 30
        init_y = sh - self.canvas_h - 90
        self.root.geometry(f"{self.canvas_w}x{self.canvas_h}+{init_x}+{init_y}")

        self.cv = tk.Canvas(
            self.root, width=self.canvas_w, height=self.canvas_h,
            bg=CHROMA_KEY, highlightthickness=0, cursor="hand2"
        )
        self.cv.pack(fill="both", expand=True)

        # 3D 核心投影中心
        self.cx = 145
        self.cy = 150
        self.fov = 300.0  # 3D 焦距 (透视强度)

        # 3D 旋转角度
        self.rot_x = 0.35  # 略微俯视角度
        self.rot_y = 0.0
        self.rot_z = 0.0

        # 全息交互状态
        self.expanded = True
        self.expand_progress = 1.0
        self.audio_level = 0.4 if is_preview else 0.0
        self.pulse = 0.0
        self.time_start = time.time()

        self.user_text = "贾维斯，启动全息三维矩阵" if is_preview else "等待指令…"
        self.jarvis_text = "全息空间投影已校准，3D 聚能核心正常运转。Sir，随时听候您的差遣。" if is_preview else "随时待命"
        self.status_text = "HOLO-3D ONLINE" if is_preview else "STANDBY"

        # 1. 生成 3D 陀螺仪多层同心环顶点
        self._init_3d_geometry()

        # 2. 生成 3D 环绕粒子群 (围绕反应堆旋转的纳米全息微粒)
        self.particles = []
        for _ in range(40):
            # 球面坐标随机分布
            theta = random.uniform(0, math.tau)
            phi = random.uniform(-math.pi/2, math.pi/2)
            rad = random.uniform(70, 115)
            self.particles.append({
                "x": rad * math.cos(phi) * math.cos(theta),
                "y": rad * math.sin(phi),
                "z": rad * math.cos(phi) * math.sin(theta),
                "spd_ang": random.uniform(0.015, 0.04),
                "r": rad,
                "theta": theta,
                "phi": phi,
                "size": random.uniform(1.2, 2.5)
            })

        # 鼠标拖拽
        self._drag_start_x = 0
        self._drag_start_y = 0
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<Double-Button-1>", self._toggle_expand)

        self._tick()

    def _init_3d_geometry(self):
        """生成 3D 几何体的三维坐标点与连线"""
        # (A) 3D 陀螺仪外环 1 (赤道环): 半径 85
        pts_eq = []
        seg = 36
        for i in range(seg):
            a = i * math.tau / seg
            pts_eq.append((85 * math.cos(a), 0, 85 * math.sin(a)))
        self.ring_equator = pts_eq

        # (B) 3D 陀螺仪倾斜环 2: 半径 76, 倾斜 45度
        pts_tilt1 = []
        for i in range(seg):
            a = i * math.tau / seg
            # 倾斜圆
            x = 76 * math.cos(a)
            y = 76 * math.sin(a) * math.sin(math.pi/4)
            z = 76 * math.sin(a) * math.cos(math.pi/4)
            pts_tilt1.append((x, y, z))
        self.ring_tilt1 = pts_tilt1

        # (C) 3D 陀螺仪倾斜环 3: 半径 68, 倾斜 -45度
        pts_tilt2 = []
        for i in range(seg):
            a = i * math.tau / seg
            x = 68 * math.cos(a)
            y = 68 * math.sin(a) * math.sin(-math.pi/4)
            z = 68 * math.sin(a) * math.cos(-math.pi/4)
            pts_tilt2.append((x, y, z))
        self.ring_tilt2 = pts_tilt2

        # (D) 3D 核心立体晶体（正二十面体黄金比例点集，代表能量核心）
        phi = (1 + math.sqrt(5)) / 2
        scale = 22.0
        # 12 个顶点
        raw_vertices = [
            (-1,  phi, 0), ( 1,  phi, 0), (-1, -phi, 0), ( 1, -phi, 0),
            (0, -1,  phi), (0,  1,  phi), (0, -1, -phi), (0,  1, -phi),
            ( phi, 0, -1), ( phi, 0,  1), (-phi, 0, -1), (-phi, 0,  1)
        ]
        self.core_vertices = [(x * scale, y * scale, z * scale) for x, y, z in raw_vertices]
        
        # 棱边连接关系 (点索引对)
        self.core_edges = [
            (0, 11), (0, 5), (0, 1), (0, 7), (0, 10),
            (1, 5), (1, 9), (1, 8), (1, 7),
            (2, 11), (2, 4), (2, 3), (2, 6), (2, 10),
            (3, 4), (3, 9), (3, 8), (3, 6),
            (4, 5), (4, 9), (4, 11),
            (5, 9), (5, 11),
            (6, 7), (6, 8), (6, 10),
            (7, 8), (7, 10),
            (8, 9), (10, 11)
        ]

    def _project_3d(self, x, y, z, rx, ry, rz):
        """
        三维欧拉角旋转 + 透视投影
        返回: (screen_x, screen_y, depth_z)
        """
        # 1. 绕 X 轴
        y1 = y * math.cos(rx) - z * math.sin(rx)
        z1 = y * math.sin(rx) + z * math.cos(rx)
        # 2. 绕 Y 轴
        x2 = x * math.cos(ry) + z1 * math.sin(ry)
        z2 = -x * math.sin(ry) + z1 * math.cos(ry)
        # 3. 绕 Z 轴
        x3 = x2 * math.cos(rz) - y1 * math.sin(rz)
        y3 = x2 * math.sin(rz) + y1 * math.cos(rz)
        z3 = z2

        # 4. 透视投影 (近大远小)
        dist = self.fov + z3
        if dist <= 1:
            dist = 1
        scale = self.fov / dist
        sx = self.cx + x3 * scale
        sy = self.cy + y3 * scale
        return sx, sy, z3

    def _on_press(self, event):
        self._drag_start_x = event.x_root - self.root.winfo_x()
        self._drag_start_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def _toggle_expand(self, _event=None):
        self.expanded = not self.expanded

    def _tick(self):
        t = time.time() - self.time_start
        self.cv.delete("all")

        # 3D 旋转角度持续演进 (差速旋转打造机械与全息感)
        self.rot_y = t * 0.95
        self.rot_x = 0.38 + math.sin(t * 0.4) * 0.12  # 轻微上下浮动
        self.rot_z = math.sin(t * 0.6) * 0.08
        self.pulse = t * 3.5

        # 展开动画进度
        target_exp = 1.0 if self.expanded else 0.0
        self.expand_progress += (target_exp - self.expand_progress) * 0.18

        if self.is_preview:
            self.audio_level = (math.sin(t * 5.2) + 1) * 0.38 + 0.12

        # ━━━━━━━━ 1. 全息翼片背板 (Holo Wing) ━━━━━━━━
        ep = self.expand_progress
        if ep > 0.02:
            panel_x = self.cx + 90
            panel_w = int(370 * ep)
            panel_y1 = self.cy - 105
            panel_y2 = self.cy + 105

            pts = [
                panel_x, panel_y1 + 16,
                panel_x + 16, panel_y1,
                panel_x + panel_w - 18, panel_y1,
                panel_x + panel_w, panel_y1 + 18,
                panel_x + panel_w, panel_y2 - 18,
                panel_x + panel_w - 18, panel_y2,
                panel_x + 16, panel_y2,
                panel_x, panel_y2 - 16,
            ]
            # 纯黑曜石底色（彻底消除背景重叠杂乱）
            self.cv.create_polygon(pts, fill=BG_PANEL, outline=BORDER_CYBER, width=1)
            
            # 全息扫描光束 (横跨翼片)
            beam_y = panel_y1 + ((t * 45) % (panel_y2 - panel_y1))
            self.cv.create_line(panel_x + 15, beam_y, panel_x + panel_w - 15, beam_y, fill=CYAN_DEEP, width=1)

            # 科技亮蓝切角线
            self.cv.create_line(panel_x + 16, panel_y1, panel_x + min(panel_w, 110), panel_y1, fill=CYAN_BRIGHT, width=2)
            self.cv.create_line(panel_x + panel_w - 60, panel_y2, panel_x + panel_w, panel_y2, fill=CYAN_BRIGHT, width=2)

            if ep > 0.45:
                # 标题状态
                self.cv.create_text(
                    panel_x + 22, panel_y1 + 22,
                    text="◆ J.A.R.V.I.S.", anchor="w",
                    fill=CYAN_BRIGHT, font=("Consolas", 10, "bold")
                )
                self.cv.create_text(
                    panel_x + panel_w - 22, panel_y1 + 22,
                    text="[3D-HOLO ONLINE]", anchor="e",
                    fill=GREEN_GLOW, font=("Consolas", 9, "bold")
                )

                # 全息频谱柱 (3D 动态波形)
                wave_y = panel_y1 + 50
                bar_count = 26
                gw = (panel_w - 44) / bar_count
                for i in range(bar_count):
                    mid_dist = abs(i - bar_count / 2) / (bar_count / 2)
                    wave_h = (math.sin(t * 7 + i * 0.45) * 0.5 + 0.5) * self.audio_level * 24 * (1.15 - mid_dist) + 2
                    bx = panel_x + 22 + i * gw
                    b_color = CYAN_BRIGHT if self.audio_level > 0.16 else CYAN_MID
                    self.cv.create_line(bx, wave_y - wave_h/2, bx, wave_y + wave_h/2, fill=b_color, width=2)

                # 用户与贾维斯对话
                self.cv.create_text(
                    panel_x + 22, panel_y1 + 78,
                    text=f"USER ▸ {self.user_text}", anchor="w", width=panel_w - 44,
                    fill=WHITE_HOT, font=("Microsoft YaHei UI", 9)
                )
                self.cv.create_text(
                    panel_x + 22, panel_y1 + 116,
                    text=self.jarvis_text, anchor="nw", width=panel_w - 44,
                    fill=CYAN_CORE, font=("Microsoft YaHei UI", 9)
                )

                # 底部操作提示
                self.cv.create_text(
                    panel_x + 22, panel_y2 - 16,
                    text="3D 透视陀螺仪 · 鼠标拖拽移动 · 双击球体收缩", anchor="w",
                    fill=TEXT_DIM, font=("Microsoft YaHei UI", 8)
                )

        # ━━━━━━━━ 2. 收集所有 3D 渲染图元并做 Z-Sorting (深度排序) ━━━━━━━━
        # 确保前景物体遮挡后景物体，展现真正 3D 空间层次
        render_queue = []

        # (1) 3D 浮游全息粒子
        for p in self.particles:
            p["theta"] += p["spd_ang"]
            px = p["r"] * math.cos(p["phi"]) * math.cos(p["theta"])
            py = p["r"] * math.sin(p["phi"])
            pz = p["r"] * math.cos(p["phi"]) * math.sin(p["theta"])
            sx, sy, sz = self._project_3d(px, py, pz, self.rot_x, self.rot_y, self.rot_z)
            render_queue.append({
                "type": "particle", "z": sz,
                "x": sx, "y": sy, "size": p["size"]
            })

        # (2) 3D 陀螺仪三环线段
        for ring_pts, r_color, r_speed, r_width in [
            (self.ring_equator, CYAN_BRIGHT, 1.0, 2),
            (self.ring_tilt1, BLUE_BRIGHT, 1.4, 2),
            (self.ring_tilt2, CYAN_MID, -0.9, 1),
        ]:
            proj_pts = []
            for (x, y, z) in ring_pts:
                sx, sy, sz = self._project_3d(x, y, z, self.rot_x, self.rot_y * r_speed, self.rot_z)
                proj_pts.append((sx, sy, sz))
            
            # 将圆环分成若干小段加入深度缓冲
            for i in range(len(proj_pts)):
                p1 = proj_pts[i]
                p2 = proj_pts[(i + 1) % len(proj_pts)]
                avg_z = (p1[2] + p2[2]) / 2.0
                render_queue.append({
                    "type": "line", "z": avg_z,
                    "x1": p1[0], "y1": p1[1],
                    "x2": p2[0], "y2": p2[1],
                    "color": r_color, "width": r_width
                })

        # (3) 3D 能量核心立体晶体 (正二十面体)
        # 呼吸跳动缩放
        core_scale = 1.0 + math.sin(self.pulse) * 0.12 + self.audio_level * 0.25
        core_proj = []
        for (vx, vy, vz) in self.core_vertices:
            sx, sy, sz = self._project_3d(
                vx * core_scale, vy * core_scale, vz * core_scale,
                -self.rot_x * 1.5, -self.rot_y * 1.8, self.rot_z * 2.0
            )
            core_proj.append((sx, sy, sz))

        for (i1, i2) in self.core_edges:
            p1 = core_proj[i1]
            p2 = core_proj[i2]
            avg_z = (p1[2] + p2[2]) / 2.0
            render_queue.append({
                "type": "core_edge", "z": avg_z,
                "x1": p1[0], "y1": p1[1],
                "x2": p2[0], "y2": p2[1]
            })

        # (4) 3D 核心发光奇点 (中心聚能核)
        csx, csy, csz = self._project_3d(0, 0, 0, self.rot_x, self.rot_y, self.rot_z)
        render_queue.append({
            "type": "core_glow", "z": csz,
            "x": csx, "y": csy, "level": self.audio_level
        })

        # ━━━━━━━━ 3. 按 Z 轴从远到近排序并绘制 ━━━━━━━━
        render_queue.sort(key=lambda item: item["z"])

        for item in render_queue:
            z = item["z"]
            # 景深衰减透明感 (近亮远暗)
            z_factor = max(0.2, min(1.0, (z + 120.0) / 240.0))

            if item["type"] == "line":
                # 后方线段变暗变细，前方高亮
                line_color = item["color"] if z > 0 else CYAN_DEEP
                line_w = item["width"] if z > 0 else 1
                self.cv.create_line(
                    item["x1"], item["y1"], item["x2"], item["y2"],
                    fill=line_color, width=line_w
                )

            elif item["type"] == "core_edge":
                edge_col = WHITE_HOT if z > 15 else (CYAN_CORE if z > -15 else CYAN_MID)
                self.cv.create_line(
                    item["x1"], item["y1"], item["x2"], item["y2"],
                    fill=edge_col, width=2 if z > 0 else 1
                )

            elif item["type"] == "particle":
                pr = item["size"] * (0.8 + 0.6 * z_factor)
                p_col = WHITE_HOT if z > 30 else (CYAN_BRIGHT if z > -20 else CYAN_DARK)
                self.cv.create_oval(
                    item["x"] - pr, item["y"] - pr,
                    item["x"] + pr, item["y"] + pr,
                    fill=p_col, outline=""
                )

            elif item["type"] == "core_glow":
                # 核心聚能光晕 (多层发光球)
                cr = 16 + math.sin(self.pulse) * 3 + item["level"] * 10
                self.cv.create_oval(item["x"] - cr * 1.5, item["y"] - cr * 1.5, item["x"] + cr * 1.5, item["y"] + cr * 1.5, fill="#04263b", outline="")
                self.cv.create_oval(item["x"] - cr, item["y"] - cr, item["x"] + cr, item["y"] + cr, fill="#007799", outline="")
                self.cv.create_oval(item["x"] - cr * 0.6, item["y"] - cr * 0.6, item["x"] + cr * 0.6, item["y"] + cr * 0.6, fill=CYAN_BRIGHT, outline="")
                self.cv.create_oval(item["x"] - cr * 0.3, item["y"] - cr * 0.3, item["x"] + cr * 0.3, item["y"] + cr * 0.3, fill=WHITE_HOT, outline="")
                # 核心微刻 "J"
                self.cv.create_text(
                    item["x"], item["y"] - 1, text="J",
                    fill="#01131c", font=("Consolas", 10, "bold")
                )

        # 30ms 持续刷新 (~33 FPS)
        self.root.after(30, self._tick)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = Hologram3DReactor(is_preview=True)
    app.run()
