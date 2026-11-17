# -*- coding: utf-8 -*-
"""
贾维斯启动画面 — 视频版启动动画 (assets/splash)
- 播放 assets/splash/frames 里的逐帧画面 + assets/splash/splash.wav 声音
- 点击画面 / 按任意键 可跳过; 播完自动淡出
- 素材缺失时自动退回旧的 HUD 自绘动画
- 可独立运行测试: python splash.py
"""
import io
import math
import random
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

BASE = Path(__file__).resolve().parent
ASSET = BASE / "assets" / "splash"
FPS = 24
DURATION = 4.0
MAX_FRAMES = int(FPS * DURATION)

# ═══════════════ 配色 (旧动画用) ═══════════════
BG   = "#080c14"
CYAN = "#00d4ff"
BLUE = "#1a6bff"
GREEN = "#39d353"
TEXT_CLR = "#c8d6e5"
DIM  = "#2a3a4f"


# ═══════════════════════════════════════════════════════
#  一、视频启动画面
# ═══════════════════════════════════════════════════════
class SplashScreen:
    """播放 assets/splash 的视频启动画面; 素材缺失则退回 HUD 动画"""

    FADE_IN = 0.35          # 淡入时长
    FADE_OUT = 0.5          # 结尾淡出时长

    def __init__(self):
        self.frames = sorted((ASSET / "frames").glob("f_*.jpg"))[:MAX_FRAMES]
        self.audio = ASSET / "splash.wav"
        self.fallback = None

        if not self.frames:
            self.fallback = HudSplash()
            return

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg="#000000")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)

        # 画面尺寸: 原片 1280x720, 屏幕放不下就按比例缩
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = 1280, 720
        scale = min(1.0, (sw - 80) / w, (sh - 110) / h)
        self.W, self.H = int(w * scale), int(h * scale)
        x, y = (sw - self.W) // 2, max(0, (sh - self.H) // 2 - 20)
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

        # 帧数据一次性读进内存 (共约 12MB), 避免播放时读盘
        self._data = [p.read_bytes() for p in self.frames]
        self._photo = None
        self._last = -1
        self._done = False
        self.n_total = len(self._data)

        self.cv = tk.Canvas(
            self.root, width=self.W, height=self.H,
            bg="#000000", highlightthickness=0, cursor="none",
        )
        self.cv.pack()
        self._img_id = self.cv.create_image(self.W // 2, self.H // 2)

        self.cv.bind("<Button-1>", lambda _: self._skip())
        self.root.bind("<Key>", lambda _: self._skip())

        self.root.deiconify()
        self._show(0)

        # 先显示首帧, 再起声音, 减少声画不同步
        self.root.after(60, self._start_playback)

    # ── 声音 ──
    def _start_audio(self):
        try:
            import pygame
            pygame.mixer.pre_init(44100, -16, 2, 512)
            pygame.mixer.init()
            pygame.mixer.music.load(str(self.audio))
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play()
        except Exception as e:
            print(f"[启动画面] 声音不可用: {e}")

    def _stop_audio(self):
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.fadeout(300)
        except Exception:
            pass

    # ── 播放 ──
    def _start_playback(self):
        self._start_audio()
        self.t0 = time.perf_counter()
        self._tick()

    def _fit(self, idx: int) -> ImageTk.PhotoImage:
        im = Image.open(io.BytesIO(self._data[idx])).convert("RGB")
        if im.size != (self.W, self.H):
            im = im.resize((self.W, self.H), Image.LANCZOS)
        return ImageTk.PhotoImage(im)

    def _show(self, idx: int):
        self._photo = self._fit(idx)
        self.cv.itemconfig(self._img_id, image=self._photo)
        self._last = idx

    def _tick(self):
        if self._done:
            return
        el = time.perf_counter() - self.t0
        idx = int(el * FPS)

        # 淡入 / 淡出
        if el < self.FADE_IN:
            self.root.attributes("-alpha", el / self.FADE_IN)
        else:
            self.root.attributes("-alpha", 1.0)

        if idx >= self.n_total:
            self._finish()
            return

        if idx != self._last:
            self._show(idx)

        # 按绝对时间排下一帧, 落后就丢帧, 不累积误差
        delay = self.t0 + (idx + 1) / FPS - time.perf_counter()
        self.root.after(max(1, int(delay * 1000)), self._tick)

    # ── 结束 ──
    def _skip(self):
        if self._done:
            return
        self._done = True
        self._stop_audio()
        # 快速淡出
        for a in (0.75, 0.5, 0.25, 0.0):
            try:
                self.root.attributes("-alpha", a)
                self.root.update()
            except Exception:
                break
            time.sleep(0.03)
        try:
            self.root.destroy()
        except Exception:
            pass

    def _finish(self):
        if self._done:
            return
        self._done = True
        self._stop_audio()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        if self.fallback is not None:
            self.fallback.run()
            return
        try:
            self.root.mainloop()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
#  二、旧的 HUD 自绘动画 (素材缺失时的兜底)
# ═══════════════════════════════════════════════════════
class HudSplash:
    """全屏科技感启动画面 (纯 Canvas 绘制, 不依赖素材)"""

    W, H = 900, 620
    DURATION = 6.8          # 总时长 (含淡出)

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x, y = (sw - self.W) // 2, (sh - self.H) // 2
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)

        self.cv = tk.Canvas(
            self.root, width=self.W, height=self.H,
            bg=BG, highlightthickness=0, cursor="none",
        )
        self.cv.pack()

        self.cv.bind("<Button-1>", lambda _: self._finish())
        self.root.bind("<Key>", lambda _: self._finish())

        self.cx, self.cy = self.W // 2, self.H // 2 - 25
        self.t0 = time.time()
        self._done = False

        self.particles = []
        for _ in range(25):
            a = random.uniform(0, math.tau)
            d = random.uniform(80, 270)
            self.particles.append(dict(
                x=self.cx + math.cos(a) * d,
                y=self.cy + math.sin(a) * d,
                vx=random.uniform(-0.35, 0.35),
                vy=random.uniform(-0.35, 0.35),
                r=random.uniform(1, 2.5),
                spd=random.uniform(1.5, 3.5),
                ph=random.uniform(0, math.tau),
            ))

        self.checks = [
            ("语音引擎",  "SpeechRecognition"),
            ("系统控制",  "Windows API"),
            ("热键监听",  "Keyboard Hook"),
            ("AI 大脑",   "Gemini Flash"),
            ("界面渲染",  "GUI Panel"),
        ]

        self.hud_labels = [
            (-220, -55, "AZM  127.4°"),
            (-220, -35, "ELV  045.2°"),
            ( 175, -55, "FRQ  2.4 GHz"),
            ( 175, -35, "MEM  NOMINAL"),
        ]

        self.root.deiconify()
        self._tick()

    @staticmethod
    def _dim(hexc: str, f: float) -> str:
        f = max(0.0, f)
        r = min(255, int(int(hexc[1:3], 16) * f))
        g = min(255, int(int(hexc[3:5], 16) * f))
        b = min(255, int(int(hexc[5:7], 16) * f))
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _ease_out(x: float) -> float:
        return 1 - (1 - x) ** 3

    @staticmethod
    def _appear(t: float, start: float, dur: float = 0.6) -> float:
        return max(0.0, min(1.0, (t - start) / dur))

    def _tick(self):
        if self._done:
            return
        t = time.time() - self.t0
        cv = self.cv
        cv.delete("all")
        cx, cy = self.cx, self.cy

        if t < 0.6:
            self.root.attributes("-alpha", t / 0.6)
        elif t > self.DURATION - 0.8:
            self.root.attributes("-alpha", max(0, (self.DURATION - t) / 0.8))
        else:
            self.root.attributes("-alpha", 1.0)

        # 1) 扫描光束
        beam1 = (t * 70) % (self.H + 60) - 30
        beam2 = self.H - ((t * 55 + 200) % (self.H + 60) - 30)
        for by in (beam1, beam2):
            cv.create_line(0, by, self.W, by, fill=self._dim(CYAN, 0.04), width=24)
            cv.create_line(0, by, self.W, by, fill=self._dim(CYAN, 0.12), width=1)

        # 2) 四角角标
        ba = self._appear(t, 0.3, 0.5)
        if ba > 0:
            bl = int(38 * ba)
            bc = self._dim(CYAN, 0.35 * ba)
            m = 22
            for (x1, y1, dx, dy) in [
                (m, m, 1, 1),
                (self.W - m, m, -1, 1),
                (m, self.H - m, 1, -1),
                (self.W - m, self.H - m, -1, -1),
            ]:
                cv.create_line(x1, y1, x1 + dx * bl, y1, fill=bc, width=2)
                cv.create_line(x1, y1, x1, y1 + dy * bl, fill=bc, width=2)

        # 3) 浮游粒子
        for p in self.particles:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            bri = 0.35 + 0.35 * math.sin(t * p["spd"] + p["ph"])
            pc = self._dim(CYAN, bri * 0.55)
            r = p["r"]
            cv.create_oval(p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r,
                           fill=pc, outline="")

        # 4) 三层旋转弧圈
        ra = self._appear(t, 0.4, 1.0)
        if ra > 0:
            for r, ext, spd, w in [(170, 50, 55, 3), (148, 35, -70, 2), (198, 28, 40, 2)]:
                cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                               outline=self._dim(CYAN, 0.07 * ra), width=1)
                for off in (0, 120, 240):
                    st = off + t * spd
                    pulse = 0.5 + 0.4 * math.sin(t * 2.5 + off * 0.017)
                    cv.create_arc(cx - r, cy - r, cx + r, cy + r,
                                  start=st, extent=ext,
                                  outline=self._dim(CYAN, ra * pulse * 0.8),
                                  width=w, style="arc")

        # 5) 雷达扫描线
        if ra > 0:
            sweep_r = 140
            sweep_a = t * 1.8
            sx = cx + math.cos(sweep_a) * sweep_r
            sy = cy - math.sin(sweep_a) * sweep_r
            cv.create_line(cx, cy, sx, sy, fill=self._dim(CYAN, 0.22 * ra), width=1)
            for i in range(1, 8):
                wa = math.degrees(sweep_a) - i * 8
                wf = 0.13 * (1 - i / 8) * ra
                cv.create_arc(cx - sweep_r, cy - sweep_r, cx + sweep_r, cy + sweep_r,
                              start=wa, extent=8,
                              outline=self._dim(CYAN, wf), width=2, style="arc")

        # 6) 中心光晕
        ca = self._appear(t, 0.8, 0.8)
        if ca > 0:
            for gr in range(55, 0, -5):
                f = (1 - gr / 55) * 0.06 * ca
                cv.create_oval(cx - gr, cy - gr, cx + gr, cy + gr,
                               fill=self._dim(CYAN, f), outline="")

        # 7) 中心 "J"
        ja = self._appear(t, 1.3, 0.5)
        if ja > 0:
            pulse = 0.8 + 0.2 * math.sin(t * 2)
            cv.create_text(cx, cy, text="J", fill=self._dim(CYAN, ja * pulse),
                           font=("Consolas", 48, "bold"))

        # 8) 水平 HUD 延伸线
        la = self._appear(t, 1.0, 0.7)
        if la > 0:
            lw = int(230 * la)
            lc = self._dim(CYAN, 0.2)
            cv.create_line(cx - 95, cy, cx - 95 - lw, cy, fill=lc, width=1)
            cv.create_line(cx + 95, cy, cx + 95 + lw, cy, fill=lc, width=1)
            for off in range(110, 110 + lw, 35):
                th = 5 if off % 70 == 0 else 3
                cv.create_line(cx - off, cy - th, cx - off, cy + th, fill=lc)
                cv.create_line(cx + off, cy - th, cx + off, cy + th, fill=lc)

        # 9) HUD 数据标签
        da = self._appear(t, 1.8, 0.6)
        if da > 0:
            for dx, dy, label in self.hud_labels:
                cv.create_text(cx + dx, cy + dy, text=label, anchor="w",
                               fill=self._dim(DIM, 0.55 * da),
                               font=("Consolas", 8))

        # 10) 标题
        ta = self._appear(t, 1.8, 1.4)
        if ta > 0:
            title = "J . A . R . V . I . S ."
            shown = title[: max(1, int(len(title) * ta))]
            cv.create_text(cx, 48, text=shown,
                           fill=self._dim(CYAN, 0.9 * min(ta * 2, 1)),
                           font=("Consolas", 24, "bold"))
            if ta > 0.25:
                sa = min(1, (ta - 0.25) / 0.4)
                cv.create_text(cx, 80, text="DESKTOP   ASSISTANT   v1.2",
                               fill=self._dim(TEXT_CLR, 0.35 * sa),
                               font=("Consolas", 10))

        # 11) 系统自检
        sy_base = cy + 135
        for i, (name, detail) in enumerate(self.checks):
            it = t - 2.8 - i * 0.45
            if it <= 0:
                continue
            ia = min(1.0, it / 0.25)
            sy_pos = sy_base + i * 28
            ready = it > 0.55
            dot_c = self._dim(GREEN if ready else CYAN, 0.9 * ia)
            cv.create_oval(cx - 185, sy_pos - 3, cx - 179, sy_pos + 3,
                           fill=dot_c, outline="")
            cv.create_text(cx - 168, sy_pos, text=f"▸ {name}", anchor="w",
                           fill=self._dim(TEXT_CLR, 0.75 * ia),
                           font=("Microsoft YaHei UI", 10))
            cv.create_text(cx - 10, sy_pos, text="· " * 10, anchor="w",
                           fill=self._dim(DIM, 0.45 * ia),
                           font=("Consolas", 9))
            if ready:
                cv.create_text(cx + 110, sy_pos, text="就绪", anchor="w",
                               fill=self._dim(GREEN, 0.9 * ia),
                               font=("Microsoft YaHei UI", 10, "bold"))
                cv.create_text(cx + 150, sy_pos, text=detail, anchor="w",
                               fill=self._dim(DIM, 0.5 * ia),
                               font=("Consolas", 9))
            else:
                dots = "." * (int(t * 6) % 4)
                cv.create_text(cx + 110, sy_pos, text=f"加载中{dots}", anchor="w",
                               fill=self._dim(CYAN, 0.5 * ia),
                               font=("Microsoft YaHei UI", 10))

        # 12) 进度条
        py = self.H - 55
        px1, px2 = cx - 200, cx + 200
        pt = self._appear(t, 0.8, 5.0)
        pp = self._ease_out(pt)
        if t > 0.8:
            cv.create_rectangle(px1, py, px2, py + 3,
                                fill=self._dim(CYAN, 0.08), outline="")
            fx = px1 + (px2 - px1) * pp
            cv.create_rectangle(px1, py, fx, py + 3,
                                fill=self._dim(CYAN, 0.55), outline="")
            if 0.01 < pp < 1:
                for gw in range(8, 0, -1):
                    gf = 0.035 * (8 - gw)
                    cv.create_rectangle(fx - gw, py - gw // 2, fx + 2, py + 3 + gw // 2,
                                        fill=self._dim(CYAN, gf), outline="")
            cv.create_text(cx, py + 18, text=f"{int(pp * 100)}%",
                           fill=self._dim(DIM, 0.6), font=("Consolas", 9))

        # 13) 完成闪光
        if t > 5.6:
            flash_t = t - 5.6
            if flash_t < 0.25:
                ff = (1 - flash_t / 0.25) * 0.07
                cv.create_rectangle(0, 0, self.W, self.H,
                                    fill=self._dim("#ffffff", ff), outline="")
            fa = min(1.0, (t - 5.7) / 0.25) if t > 5.7 else 0
            if fa > 0:
                blink = 0.7 + 0.3 * math.sin(t * 10)
                cv.create_text(cx, py - 22, text="S Y S T E M   O N L I N E",
                               fill=self._dim(GREEN, fa * blink),
                               font=("Consolas", 13, "bold"))

        # 14) 页脚
        if t > 2.0:
            ft = min(1.0, (t - 2) / 0.5)
            cv.create_text(cx, self.H - 20,
                           text="JARVIS DESKTOP SYSTEM  ·  贾维斯桌面助手",
                           fill=self._dim(DIM, 0.4 * ft),
                           font=("Consolas", 8))

        if t < self.DURATION and not self._done:
            self.root.after(16, self._tick)
        elif not self._done:
            self._finish()

    def _finish(self):
        if self._done:
            return
        self._done = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        try:
            self.root.mainloop()
        except Exception:
            pass


# ═══════════════ 独立测试 ═══════════════
if __name__ == "__main__":
    SplashScreen().run()
    print("✔ 启动画面结束")
