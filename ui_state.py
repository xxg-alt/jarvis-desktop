"""界面状态机 + 对账（reconcile）。

为什么要有这个文件
------------------
原来"界面该长什么样"在系统里有四本互不同步的账：
    1. self.mode        （orb / web / capsule / fullscreen）
    2. self.ui_style    （web / new / classic，语义和 mode 重叠）
    3. 各载体的 alive / visible 标志
    4. Windows 里真实的窗口状态（可见 / 最小化）
四本账靠异步消息同步（socket、after、子进程），失败了又被静默吞掉，
于是任何一处失败都表现为同一种症状：**屏幕上没有可交互的界面**。
用户嘴里的"退出""卡死"，绝大多数是这一件事的不同长相。

这里把账收敛成一本：
    - 唯一权威 target：现在应该是哪种形态
    - 对账 reconcile()：用 Win32 真的去数窗口，而不是相信标志位
    - 自愈：目标形态不在屏幕上就修；连续修不好就降级到悬浮球

约定：reconcile 必须在 Tk 主线程里调用（probe 会用到 tk 的 winfo 系列）。
"""
import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.windll.user32


# ── Win32：数真实窗口 ──
class WinInfo:
    __slots__ = ("hwnd", "title", "visible", "iconic")

    def __init__(self, hwnd, title, visible, iconic):
        self.hwnd = hwnd
        self.title = title
        self.visible = visible
        self.iconic = iconic

    def __repr__(self):
        return f"<{self.title!r} vis={self.visible} min={self.iconic}>"


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def top_windows():
    """枚举所有带标题的顶层窗口。"""
    out = []

    def _cb(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value
            if title:
                out.append(WinInfo(hwnd, title,
                                   bool(_user32.IsWindowVisible(hwnd)),
                                   bool(_user32.IsIconic(hwnd))))
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception:
        return []
    return out


def find_windows(title_part):
    """标题含 title_part 的顶层窗口。"""
    return [w for w in top_windows() if title_part in w.title]


def on_screen(title_part):
    """标题匹配的窗口里，是否有"真的在屏幕上"的（可见且非最小化）。

    注意不能只看 IsWindowVisible：最小化的窗口它依然返回 True。
    """
    return any(w.visible and not w.iconic for w in find_windows(title_part))


def restore_window(hwnd):
    """把窗口从"最小化/隐藏"状态拉回屏幕。

    为什么不用 pywebview 的 win.show()：它内部是 ShowWindow(SW_SHOW)，
    对已经最小化的窗口不会还原（Windows 要求 SW_RESTORE）。这里直接调
    Win32，同步生效，不用等子进程回消息。
    """
    try:
        _user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        _user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# ── 状态机 ──
class UiStateMachine:
    """唯一权威的目标形态 + 对账 + 自愈 + 降级。"""

    def __init__(self, log=print, on_degrade=None, max_fail=2, quiet=3.0, enabled=True):
        self.log = log
        self.on_degrade = on_degrade
        self.max_fail = max_fail
        self.quiet = quiet
        self.enabled = enabled

        self.target = "orb"
        self._carriers = {}          # mode -> (probe, heal)
        self._fail_streak = 0
        self._quiet_until = 0.0

        # 统计，方便判断"到底自愈了多少次"
        self.heal_total = 0
        self.degrade_total = 0
        self.last_check = 0.0

    # ── 注册载体 ──
    def register(self, mode, probe, heal):
        """probe() -> bool 实际是否在屏幕上；heal() -> bool 是否修好。"""
        self._carriers[mode] = (probe, heal)

    # ── 设定目标 ──
    def set_target(self, mode, reason=""):
        """切换目标形态。切换后进入静默期，避免"正在切"被对账误判成"切坏了"。"""
        if mode not in self._carriers:
            return
        if mode == self.target:
            self._quiet_until = time.time() + self.quiet
            return
        self.log(f"[对账] 目标: {self.target} -> {mode}" + (f"（{reason}）" if reason else ""))
        self.target = mode
        self._fail_streak = 0
        self._quiet_until = time.time() + self.quiet

    # ── 对账 ──
    def reconcile(self):
        """对一次账。返回目标形态当前是否真的成立。"""
        if not self.enabled:
            return True
        now = time.time()
        self.last_check = now
        if now < self._quiet_until:
            return True

        pair = self._carriers.get(self.target)
        if pair is None:
            return True
        probe, heal = pair

        try:
            ok = bool(probe())
        except Exception as exc:
            self.log(f"[对账] 探测 {self.target} 出错: {exc}")
            ok = False

        if ok:
            if self._fail_streak:
                self.log(f"[对账] {self.target} 已恢复（此前连续 {self._fail_streak} 次不符）")
            self._fail_streak = 0
            return True

        self._fail_streak += 1
        self.log(f"[对账] {self.target} 不在屏幕上（连续 {self._fail_streak} 次），开始自愈")

        try:
            heal()
        except Exception as exc:
            self.log(f"[对账] 自愈 {self.target} 抛异常: {exc}")
        self.heal_total += 1

        # 修好没有由"下一拍探测"来判定，不信 heal 的返回值：
        # 显示指令可能是异步的，立刻复检会把"正在恢复"误判成"修不好"，
        # 于是刚切过去就被降级（这是第一版的真实翻车点）。
        if self._fail_streak >= self.max_fail:
            self.degrade_total += 1
            self.log(f"[对账] {self.target} 连续 {self._fail_streak} 次自愈失败，降级到悬浮球")
            self.target = "orb"
            self._fail_streak = 0
            self._quiet_until = time.time() + self.quiet
            try:
                if self.on_degrade:
                    self.on_degrade()
                else:
                    self._carriers["orb"][1]()
            except Exception as exc:
                self.log(f"[对账] 降级失败: {exc}")
        return False

    def snapshot(self):
        """给日志/诊断用的一行摘要。"""
        return (f"目标={self.target} 自愈={self.heal_total} 降级={self.degrade_total} "
                f"连续失败={self._fail_streak}")

def _selftest():
    # 模拟真实的异步：heal 下达指令，窗口在"下一拍"才真的出现
    state = {"ok": False, "pending": False, "heal_ok": True}

    def probe():
        if state["pending"]:
            state["ok"] = True
            state["pending"] = False
        return state["ok"]

    def heal():
        state["pending"] = state["heal_ok"]
        return state["heal_ok"]

    m = UiStateMachine(log=print, quiet=0.0)
    m.register("web", probe, heal)
    m.register("orb", lambda: True, lambda: True)
    m.set_target("web", "冒烟")

    print("场景 1a：窗口缺失 ->", m.reconcile(), "|", m.snapshot())
    print("场景 1b：下一拍已恢复 ->", m.reconcile(), "|", m.snapshot())

    state["ok"] = False
    state["heal_ok"] = False
    print("场景 2a：自愈失败1次 ->", m.reconcile(), "|", m.snapshot())
    print("场景 2b：自愈失败2次 ->", m.reconcile(), "|", m.snapshot())

    print()
    print("窗口探测自检：")
    for w in find_windows("Jarvis")[:5]:
        print("   ", w)
    print("   抓到在屏幕上的 Jarvis 窗口:", on_screen("Jarvis"))


if __name__ == "__main__":
    _selftest()