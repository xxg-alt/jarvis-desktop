# -*- coding: utf-8 -*-
"""运行时可观测性：崩溃栈 + 线程看门狗 + 心跳

为什么需要这一层
----------------
程序是用 pythonw.exe 启动的（无控制台）。一旦出现下面三种情况，外部只会看到
"窗口还在、什么都不动"或者"程序没了"，完全无从判断死在哪：

  1. native 层崩溃（ctranslate2 / WebView2 / PortAudio）—— 没有 Python traceback
  2. 子线程抛未捕获异常 —— pythonw 下 stderr 丢失，线程静默死亡
  3. 线程死锁/永久阻塞 —— 进程活着但不干活，没有任何输出

本模块把三路证据统一落到 jarvis-gui.log：
  - faulthandler 捕获 native 崩溃时的全部线程栈
  - threading.excepthook 捕获子线程异常
  - 看门狗定期检查心跳，卡住时主动 dump 所有线程栈

对外接口
--------
    enable(log_file, crash_file=None)   启动时调用一次
    mark(name)                          热点线程打心跳
    since(name) -> 距上次心跳多少秒
    start_watchdog(get_problems, log_path, interval=2.0)
"""
import faulthandler
import io
import os
import sys
import threading
import time
import traceback

_LOCK = threading.Lock()
_LAST_BEAT = {}
_STARTED = {"watchdog": False}
_LAST_DUMP = [0.0]
_DUMP_COOLDOWN = 30.0      # 两次 dump 之间至少隔 30 秒，避免刷爆日志
_CRASH_FH = [None]         # 持有 faulthandler 的文件句柄，不能被 GC


def _append(path, text):
    try:
        with open(path, "a", encoding="utf-8", errors="ignore") as f:
            f.write(text)
            f.flush()
    except Exception:
        pass


def enable(log_file, crash_file=None):
    """接管三路异常/崩溃输出。启动时调用一次即可。"""
    log_file = str(log_file)
    crash_file = str(crash_file or os.path.join(os.path.dirname(log_file), "jarvis-crash.log"))

    # 1) native 崩溃（C 层段错误等）——faulthandler 能抓到，普通 try/except 抓不到
    try:
        _CRASH_FH[0] = open(crash_file, "a", encoding="utf-8", errors="ignore")
        faulthandler.enable(file=_CRASH_FH[0], all_threads=True)
    except Exception:
        pass

    # 2) 主线程未捕获异常
    def _sys_hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _append(log_file, "\n[未捕获异常-主线程]\n" + text)

    sys.excepthook = _sys_hook

    # 3) 子线程未捕获异常（这才是"某个线程悄悄死掉"的真凶）
    def _thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "?")
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                  args.exc_traceback))
        _append(log_file, f"\n[未捕获异常-线程 {name}]\n" + text)

    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass


def mark(name):
    """热点线程调用，记录一次心跳。"""
    with _LOCK:
        _LAST_BEAT[name] = time.time()


def since(name):
    """距该心跳多少秒；从未打过返回一个很大的值。"""
    with _LOCK:
        t = _LAST_BEAT.get(name)
    if t is None:
        return 1e9
    return time.time() - t


def dump_all_threads(log_path, reason=""):
    """把所有线程栈写进日志（含 Python 帧和 C 层状态）。"""
    now = time.time()
    if now - _LAST_DUMP[0] < _DUMP_COOLDOWN:
        return False
    _LAST_DUMP[0] = now

    buf = io.StringIO()
    buf.write(f"\n===== [看门狗] {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"检测到无响应：{reason} =====\n")
    buf.write("线程清单：\n")
    for th in threading.enumerate():
        buf.write(f"  - {th.name} alive={th.is_alive()} daemon={th.daemon}\n")
    buf.write("\n全部线程栈：\n")
    try:
        faulthandler.dump_traceback(file=buf, all_threads=True)
    except Exception as e:
        buf.write(f"  (dump 失败: {e})\n")
    buf.write("===== [看门狗] 结束 =====\n\n")
    _append(log_path, buf.getvalue())
    return True


def start_watchdog(get_problems, log_path, interval=2.0, grace=6.0):
    """后台看门狗。

    get_problems() 应返回问题描述列表（空列表表示一切正常）。
    同一类问题持续超过 grace 秒，就把全部线程栈写进日志。
    """
    if _STARTED["watchdog"]:
        return
    _STARTED["watchdog"] = True

    active = {}
    lock = threading.Lock()

    def _loop():
        while True:
            time.sleep(interval)
            try:
                problems = list(get_problems() or [])
            except Exception as e:
                problems = [f"看门狗自检异常: {type(e).__name__}: {e}"]
            now = time.time()
            with lock:
                for p in problems:
                    active.setdefault(p, now)
                for p in list(active):
                    if p not in problems:
                        active.pop(p, None)
            for p, t0 in list(active.items()):
                if now - t0 >= grace:
                    dump_all_threads(log_path, p)
                    break

    threading.Thread(target=_loop, name="jarvis-watchdog", daemon=True).start()
