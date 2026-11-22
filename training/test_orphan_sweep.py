"""回归：退出残留 web 子进程的清扫逻辑。

背景
----
用户反馈"每退一次贾维斯，任务栏/桌面就多一个 Python 脚本图标"。
根因：quit_app 先 web_capsule.stop() → alive=False，紧接着主进程 os._exit(0)；
中间那一拍 Tk 主循环的"胶囊掉线自动重启"看门狗看到一个死掉的胶囊，
立刻拉起了新的 web_capsule.py --child —— 主进程随即退出，新子进程成了孤儿。
Windows 用被执行的 .py 文件做任务栏图标，于是"多出一个脚本"。

修法（本测试守护的两条不变量）
- 孤儿（父进程已死）必须被杀掉；
- 父进程还活着的 web 子进程不能被误杀（那可能是另一个正在运行的贾维斯实例）。
"""
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "jarvis_gui.py"

DUMMY = [sys.executable, "-c", "import time; time.sleep(45)",
         "web_capsule.py", "--child", "1"]


def load_sweep():
    """从 jarvis_gui.py 里取出真实函数源码执行，避免 import 整个 GUI 模块。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_sweep_web_children")
    mod = ast.Module(body=[fn], type_ignores=[])
    ns = {"os": os, "psutil": psutil}
    exec(compile(mod, str(SRC), "exec"), ns)
    return ns["_sweep_web_children"]


def find_dummy():
    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        if p.info["pid"] == me:
            continue
        cmd = " ".join(p.info.get("cmdline") or [])
        if "web_capsule.py" in cmd and "--child" in cmd and "time.sleep(45)" in cmd:
            return p.info["pid"]
    return None


def main():
    sweep = load_sweep()
    fails = []

    # ── 用例 1：孤儿必须被杀 ──
    launcher = Path(__file__).with_name("_launch_orphan.py")
    launcher.write_text(
        "import subprocess, sys, os\n"
        "subprocess.Popen(%r, creationflags=0x00000008 | 0x00000200)\n" % (DUMMY,),
        encoding="utf-8")
    try:
        subprocess.run([sys.executable, str(launcher)], timeout=15, check=False)
        time.sleep(1.2)
        pid = find_dummy()
        if pid is None:
            fails.append("孤儿用例：没能造出 dummy 进程")
        else:
            ppid = psutil.Process(pid).ppid()
            parent_alive = ppid and psutil.pid_exists(ppid)
            killed = sweep()
            time.sleep(0.5)
            gone = not psutil.pid_exists(pid)
            print(f"[用例1] pid={pid} ppid={ppid}(alive={parent_alive}) "
                  f"sweep_killed={killed} 已消失={gone}")
            if not gone:
                fails.append("孤儿用例：孤儿没被清掉")
    finally:
        launcher.unlink(missing_ok=True)

    # ── 用例 2：父进程还活着的不能被误杀 ──
    child = subprocess.Popen(DUMMY, creationflags=0x00000008 | 0x00000200)
    time.sleep(1.0)
    alive_before = child.poll() is None
    killed2 = sweep()
    time.sleep(0.4)
    alive_after = child.poll() is None
    print(f"[用例2] 自己的子进程 存活前={alive_before} sweep_killed={killed2} 存活后={alive_after}")
    if not alive_before:
        fails.append("用例2：dummy 没起来，无法判定")
    elif not alive_after:
        fails.append("用例2：误杀了父进程仍在的子进程")
    try:
        child.kill()
    except Exception:
        pass

    # ── 用例 3：源码里退出旗标必须存在，且重启分支要看它 ──
    src = SRC.read_text(encoding="utf-8")
    checks = [
        ("quit_app 立旗", "self._quitting = True" in src),
        ("看门狗看旗", 'getattr(self, "_quitting", False)' in src and "不许再起新子进程" in src),
        ("重建入口看旗", "退出流程里任何一次重建都等于制造孤儿进程" in src),
        ("退出前兜底清扫", "_sweep_web_children(include_own=True)" in src),
    ]
    for name, ok in checks:
        print(f"[用例3] {name}: {'OK' if ok else 'FAIL'}")
        if not ok:
            fails.append(f"用例3：{name} 缺失")

    print("\n结果：", "全部通过" if not fails else "失败 → " + "; ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
