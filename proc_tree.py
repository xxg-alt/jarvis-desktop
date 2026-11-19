"""安全的进程树回收。

为什么不用 `taskkill /F /T /PID`：
    /T 是按 **PPID** 递归构树的，而 Windows 的 PID 会被回收复用。
    当我们要清理的子进程已经退出（PID 失效），这个 PID 可能已经被系统
    分配给了别的进程；同时主进程自己的父进程（启动它的 shell）通常也早已退出、
    PID 同样可能被复用。两者一撞，taskkill 就把**主进程**当成目标子树的成员
    一起 /F 强杀 —— 表现就是"按 ESC 之后程序自己没了"，而且 stderr 一片空白，
    因为强杀不给 Python 任何抛异常的机会。

psutil 的 children() 会校验 create_time，PID 复用不会被误认；
再加一道显式护栏：自己和自己的所有祖先，永远不杀。
"""
import os
import sys

try:
    import psutil
except Exception:      # pragma: no cover - psutil 缺失时退化
    psutil = None


def _protected_pids():
    """自己 + 自己的全部祖先，任何情况下都不允许被杀。"""
    out = {os.getpid()}
    if psutil is None:
        return out
    try:
        p = psutil.Process(os.getpid())
        for a in p.parents():
            out.add(a.pid)
    except Exception:
        pass
    return out


def kill_tree(pid, timeout=2.0):
    """回收 pid 及其真实后代。返回实际处理的进程数。

    只杀"确实是它的后代、且创建时间对得上"的进程；
    自己与祖先一律跳过。任何一步失败都不向上抛，避免拖垮调用方。
    """
    if not pid:
        return 0
    protect = _protected_pids()
    if pid in protect:
        return 0

    if psutil is None:
        # 没有 psutil 就只杀目标本身，绝不盲目杀树
        try:
            import subprocess
            exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                               "System32", "taskkill.exe")
            subprocess.run([exe, "/F", "/PID", str(pid)],
                           capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return 1
        except Exception:
            return 0

    try:
        root = psutil.Process(pid)
    except Exception:
        return 0          # 目标早没了，什么都不用做

    victims = []
    try:
        # psutil 内部按 create_time 校验父子关系，PID 复用不会被误认
        for c in root.children(recursive=True):
            if c.pid not in protect:
                victims.append(c)
    except Exception:
        pass

    # 目标本身放最后，先收子进程（WebView2）再收宿主
    victims.append(root)

    # 诊断：把"到底要杀谁"写进日志。主进程被误杀时 stderr 是空的，
    # 只有这行能留下证据。
    try:
        print("[proc_tree] 自己=%d 目标=%d 待杀=%s" % (
            os.getpid(), pid, [v.pid for v in victims]), flush=True)
    except Exception:
        pass

    n = 0
    for v in victims:
        try:
            v.terminate()
            n += 1
        except Exception:
            continue

    try:
        _gone, alive = psutil.wait_procs(victims, timeout=timeout)
        for v in alive:
            try:
                v.kill()
            except Exception:
                pass
    except Exception:
        pass
    return n


if __name__ == "__main__":
    import subprocess
    import time

    print("psutil:", "有" if psutil else "缺失")
    print("受保护 PID（自己+祖先）:", sorted(_protected_pids()))

    # 造一个"父带子"的结构，验证只杀该子树、不动自己
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
         "time.sleep(60)"])
    time.sleep(1.5)
    kids_before = []
    if psutil:
        kids_before = [c.pid for c in psutil.Process(parent.pid).children(recursive=True)]
    print("目标 pid=%d，其后代=%s" % (parent.pid, kids_before))

    n = kill_tree(parent.pid)
    time.sleep(0.5)
    print("kill_tree 处理了 %d 个进程" % n)
    print("目标存活:", parent.poll() is None)
    if psutil:
        for pid in kids_before:
            print("  后代 %d 存活: %s" % (pid, psutil.pid_exists(pid)))

    # 护栏：拿自己的 PID 去杀，必须什么都没发生
    before = os.getpid()
    kill_tree(before)
    print("对自己调用 kill_tree 后仍然活着:", psutil.pid_exists(before) if psutil else True)

    # 失效 PID：必须安静返回 0
    print("对不存在的 PID 调用:", kill_tree(999999))
