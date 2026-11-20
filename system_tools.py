"""第二步：给大模型装上手 —— 三个真实系统工具。

设计原则：
- 入参与返回值都是标准类型（dict / list / str / int / bool），可直接 JSON 序列化
- 文件操作严格限制在 Desktop / Downloads / Documents 三个目录，越界一律拒绝
- 不做删除；移动不覆盖（重名自动加序号）
- PowerShell 只跑单条命令（管道/重定向在上一层已被过滤），黑名单拦高危操作
- 任何失败都返回 ok=False + 可朗读的中文说明，绝不把异常栈抛给用户

统一返回结构：
    {"ok": bool, "summary": str, "data": Any, "danger": bool}
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

MAX_LIST = 200
MAX_SEARCH = 100

# 中文 Windows 的真实文件夹名仍是 Desktop/Downloads/Documents（显示名才是中文），
# 中文别名一并保留，便于在特殊环境下也能命中。
ALLOWED_FOLDER_NAMES = ("Desktop", "Downloads", "Documents", "桌面", "下载", "文档")

# 大模型经常回英文目录名（downloads / desktop），这里统一映射到真实文件夹名。
FOLDER_ALIASES = {
    "desktop": "Desktop", "桌面": "Desktop",
    "downloads": "Downloads", "download": "Downloads", "下载": "Downloads",
    "documents": "Documents", "document": "Documents", "文档": "Documents",
}


def _alias_folder(path_like):
    """把单独的目录别名换成授权目录的绝对路径；带分隔符的原样返回。"""
    raw = str(path_like).strip().strip("\\/")
    if not raw or "/" in raw or "\\" in raw:
        return path_like
    name = FOLDER_ALIASES.get(raw.lower())
    return Path.home() / name if name else path_like


def _result(ok: bool, summary: str, data: Any = None, danger: bool = False) -> dict:
    """构造统一返回体。"""
    return {"ok": bool(ok), "summary": str(summary), "data": data, "danger": bool(danger)}


def allowed_roots() -> list:
    """当前用户的授权目录（只返回真实存在的）。"""
    home = Path.home()
    roots = []
    for name in ALLOWED_FOLDER_NAMES:
        candidate = home / name
        try:
            if candidate.is_dir():
                resolved = candidate.resolve()
                if resolved not in roots:
                    roots.append(resolved)
        except OSError:
            continue
    return roots


def _inside(path_like) -> Path | None:
    """把路径解析成绝对路径，并确认落在授权目录内；越界返回 None。

    先 resolve() 展开 `..` 与相对路径，避免 `../../Windows` 这类绕过。
    相对路径以 Downloads 为基准，避免调用方猜不到根。
    """
    if not path_like:
        return None
    path_like = _alias_folder(path_like)
    try:
        target = Path(str(path_like)).expanduser()
        if not target.is_absolute():
            target = Path.home() / "Downloads" / target
        target = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    for root in allowed_roots():
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    return None


def _fmt_size(num: float) -> str:
    """人类可读的容量。"""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ---------------------------------------------------------------- 工具 1：文件管家

def file_manage(action, src=None, dst=None, pattern=None, folder=None) -> dict:
    """列出 / 搜索 / 归类移动文件，全部限定在授权目录内。"""
    act = str(action or "").strip().lower()

    if act == "list":
        target = _inside(folder) if folder else _inside(Path.home() / "Downloads")
        if target is None:
            return _result(False, "我只能查看桌面、下载、文档这三个目录，这个位置不在授权范围。")
        if not target.is_dir():
            return _result(False, f"{target.name} 不是一个文件夹。")

        items = []
        try:
            for entry in target.iterdir():
                try:
                    stat = entry.stat()
                    is_dir = entry.is_dir()
                    items.append({
                        "name": entry.name,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else int(stat.st_size),
                        "size_text": "文件夹" if is_dir else _fmt_size(stat.st_size),
                        "mtime": _fmt_time(stat.st_mtime),
                    })
                except OSError:
                    continue
        except OSError as exc:
            return _result(False, f"读不了这个目录：{exc}")

        items.sort(key=lambda row: row["mtime"], reverse=True)
        shown = items[:MAX_LIST]
        return _result(
            True,
            f"{target.name} 下共 {len(items)} 项，最近修改的 {len(shown)} 项已经列出来了。",
            {"folder": str(target), "total": len(items), "items": shown},
        )

    if act == "search":
        pat = str(pattern or "").strip()
        if not pat:
            return _result(False, "要按什么找？比如 *.pdf，或者 *简历*。")

        scope = _inside(folder) if folder else None
        if folder and scope is None:
            return _result(False, "只能搜索桌面、下载、文档这三个目录。")
        roots = [scope] if scope else allowed_roots()
        if not roots:
            return _result(False, "这台机器上没有可搜索的授权目录。")

        hits = []
        for root in roots:
            try:
                iterator = root.rglob(pat)
            except (OSError, ValueError):
                continue
            for path in iterator:
                try:
                    if path.is_file():
                        stat = path.stat()
                        hits.append({
                            "path": str(path),
                            "size_text": _fmt_size(stat.st_size),
                            "mtime": _fmt_time(stat.st_mtime),
                        })
                except OSError:
                    continue
                if len(hits) >= MAX_SEARCH:
                    break
            if len(hits) >= MAX_SEARCH:
                break

        if not hits:
            return _result(True, f"授权目录里没有找到匹配 {pat} 的文件。",
                           {"pattern": pat, "items": []})
        return _result(True, f"找到 {len(hits)} 个匹配 {pat} 的文件。",
                       {"pattern": pat, "items": hits})

    if act == "move":
        src_path = _inside(src)
        if src_path is None:
            return _result(False, "源文件不在桌面、下载、文档里，授权范围之外的文件我不动。")
        if not src_path.exists():
            return _result(False, f"没找到 {Path(str(src)).name}。")
        if not src_path.is_file():
            return _result(False, "目前只支持移动单个文件，还不支持整个文件夹。")

        if dst:
            dst_dir = _inside(dst)
            if dst_dir is None:
                return _result(False, "目标位置不在授权范围（只能是桌面、下载、文档）。")
            if dst_dir.is_file():
                return _result(False, "目标给的是一个文件，我需要一个目录。")
        else:
            dst_dir = _inside(Path.home() / "Downloads")
            if dst_dir is None:
                return _result(False, "找不到下载目录。")

        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _result(False, f"建不了目标目录：{exc}")

        # 绝不覆盖：重名自动加 (1)(2)…
        target = dst_dir / src_path.name
        if target.exists():
            stem, suffix = target.stem, target.suffix
            for index in range(1, 1000):
                candidate = dst_dir / f"{stem} ({index}){suffix}"
                if not candidate.exists():
                    target = candidate
                    break
            else:
                return _result(False, "目标目录同名文件太多了，换个目录吧。")

        try:
            shutil.move(str(src_path), str(target))
        except OSError as exc:
            return _result(False, f"移动失败：{exc}")

        return _result(
            True,
            f"已经把 {src_path.name} 移到 {dst_dir.name}。",
            {"src": str(src_path), "dst": str(target)},
            danger=True,
        )

    return _result(False, "文件这块我只会「列目录、按名字找、归到某个目录」三件事。")


# ---------------------------------------------------------- 工具 2：系统状态感知

def inspect_system(mode, target=None) -> dict:
    """查内存榜 / CPU 榜 / 端口占用。只用 psutil，不弹窗、不改系统。"""
    try:
        import psutil
    except ImportError:
        return _result(False, "缺少 psutil，读不到系统状态。")

    kind = str(mode or "").strip().lower()

    if kind == "top_memory":
        rows = []
        for proc in psutil.process_iter(["name", "pid", "memory_info"]):
            try:
                info = proc.info
                memory = info.get("memory_info")
                if memory is None:
                    continue
                rows.append({
                    "name": info.get("name") or "未知",
                    "pid": info.get("pid"),
                    "memory_mb": round(memory.rss / (1024 * 1024), 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        rows.sort(key=lambda row: row["memory_mb"], reverse=True)
        top = rows[:5]
        if not top:
            return _result(False, "没读到进程信息。")
        desc = "、".join(f"{row['name']}({row['memory_mb']}MB)" for row in top)
        return _result(True, f"最吃内存的前 5 个进程：{desc}。", {"mode": kind, "items": top})

    if kind == "top_cpu":
        procs = list(psutil.process_iter(["name", "pid"]))
        # cpu_percent 首次调用恒为 0，必须先预热再间隔采样
        for proc in procs:
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        time.sleep(0.5)

        rows = []
        for proc in procs:
            try:
                cpu = proc.cpu_percent(interval=None)
                rows.append({
                    "name": proc.info.get("name") or "未知",
                    "pid": proc.info.get("pid"),
                    "cpu_percent": round(float(cpu), 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        rows.sort(key=lambda row: row["cpu_percent"], reverse=True)
        top = rows[:5]
        if not top:
            return _result(False, "没读到进程信息。")
        desc = "、".join(f"{row['name']}({row['cpu_percent']}%)" for row in top)
        return _result(True, f"最吃 CPU 的前 5 个进程：{desc}。", {"mode": kind, "items": top})

    if kind == "port":
        try:
            port = int(target)
        except (TypeError, ValueError):
            return _result(False, "端口号得是数字，比如 8080。")
        if not (0 < port < 65536):
            return _result(False, "端口号超出范围了（1-65535）。")

        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, OSError):
            return _result(False, "没有权限读取网络连接表。")

        hits = []
        for conn in conns:
            try:
                if conn.laddr and conn.laddr.port == port:
                    hits.append({"pid": conn.pid, "state": conn.status,
                                 "local": f"{conn.laddr.ip}:{conn.laddr.port}"})
                elif conn.raddr and conn.raddr.port == port:
                    hits.append({"pid": conn.pid, "state": conn.status,
                                 "remote": f"{conn.raddr.ip}:{conn.raddr.port}"})
            except (AttributeError, TypeError):
                continue

        if not hits:
            return _result(True, f"当前没有进程在占用 {port} 端口。",
                           {"mode": kind, "port": port, "items": []})

        for hit in hits[:8]:
            name = "未知"
            pid = hit.get("pid")
            if pid:
                try:
                    name = psutil.Process(pid).name()
                except Exception:
                    name = "未知"
            hit["name"] = name

        names = []
        for hit in hits[:5]:
            names.append(f"{hit['name']}(PID {hit.get('pid')})")
        return _result(True, f"占用 {port} 端口的是：{'、'.join(names)}。",
                       {"mode": kind, "port": port, "items": hits[:8]})

    return _result(False, "系统状态我只会查「内存榜、CPU 榜、端口占用」三种。")


# ------------------------------------------------- 工具 3：受限 PowerShell 自动化

# 黑名单：这些动作不可逆或会改动系统级设置，直接拒绝（不给确认机会）
_BLOCKED_PATTERNS = re.compile(
    r"(shutdown|Stop-Computer|Restart-Computer|"
    r"Format-Volume|format\s+[a-zA-Z]:|"
    r"Remove-Item\s+-Recurse|Remove-Item\s+-Force|del\s+/s|rd\s+/s|rmdir\s+/s|"
    r"reg\s+add|reg\s+delete|Set-ItemProperty|New-ItemProperty|"
    r"Set-Service|New-Service|Remove-Service|sc\s+(config|delete|create)|"
    r"Set-ExecutionPolicy|"
    r"takeown|icacls|cipher\s+/w|"
    r"Invoke-Expression|iex\b|Invoke-Command|Start-Process\s+-Verb\s+RunAs|"
    r"Clear-Disk|Initialize-Disk|bcdedit|"
    r"New-LocalUser|Set-LocalUser|Remove-LocalUser|"
    r"net\s+user|net\s+localgroup)",
    re.I,
)

# 只读命令放行（免确认）；其余一律标 danger 交第一步护栏
_READONLY_PREFIX = (
    "get-", "test-", "measure-", "select-", "where-", "resolve-", "find-",
    "compare-", "getmac", "ipconfig", "whoami", "hostname", "systeminfo",
    "tasklist", "netstat", "driverquery", "chkdsk", "ver", "date", "time",
    "ping", "nslookup", "tracert", "dir", "type ", "more ", "echo ",
)


def is_readonly_command(cmd) -> bool:
    """判断是否为只读查询命令：只读放行，其余交给第一步护栏确认。"""
    return str(cmd or "").strip().lower().startswith(_READONLY_PREFIX)


def _decode_output(data: bytes) -> str:
    """PowerShell 输出编码不稳定（UTF-8 / GBK 都可能），依次尝试解码。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "utf-16-le"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def _powershell_exe() -> str:
    """定位 PowerShell 可执行文件。

    优先用绝对路径的系统 PowerShell 5.1：某些受限环境（沙箱、精简镜像、
    被裁剪过的 PATH）里 `powershell` 这个名字根本解析不到，直接传名字会 WinError 2。
    """
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidates = (
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        os.path.join(system_root, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe"),
    )
    for path in candidates:
        if os.path.exists(path):
            return path
    for name in ("pwsh.exe", "powershell.exe"):
        hit = shutil.which(name)
        if hit:
            return hit
    return "powershell.exe"


def run_powershell(command) -> dict:
    """执行单条 PowerShell 命令；返回标准输出文本。"""
    cmd = str(command or "").strip()
    if not cmd:
        return _result(False, "要执行什么命令？")
    if len(cmd) > 1000:
        return _result(False, "命令太长了，拆成几条说吧。")
    if _BLOCKED_PATTERNS.search(cmd):
        return _result(False, "这条命令会关机、删文件或改系统设置，这个我不做。")

    readonly = is_readonly_command(cmd)
    try:
        proc = subprocess.run(
            [_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return _result(False, "命令跑了 20 秒还没结束，我先把它停了。")
    except OSError as exc:
        return _result(False, f"没法启动 PowerShell：{exc}")

    stdout = _decode_output(proc.stdout)
    stderr = _decode_output(proc.stderr)

    if proc.returncode != 0 and not stdout:
        return _result(False, f"命令执行失败（返回码 {proc.returncode}）：{stderr[:300] or '没有更多信息'}")

    preview = stdout[:200].replace("\r", "").replace("\n", " ⏎ ") if stdout else ""
    if stdout:
        summary = f"命令执行完毕，输出：{preview}"
    else:
        summary = "命令执行完毕，但没有输出。"

    return _result(
        True,
        summary,
        {"command": cmd, "returncode": proc.returncode,
         "stdout": stdout[:2000], "stderr": stderr[:600]},
        danger=not readonly,
    )


if __name__ == "__main__":
    # 手动冒烟：python system_tools.py
    print("授权目录:", [str(r) for r in allowed_roots()])
    print("列下载目录:", file_manage("list")["summary"])
    print("搜索 pdf:", file_manage("search", pattern="*.pdf")["summary"])
    print("内存榜:", inspect_system("top_memory")["summary"])
    print("端口 8080:", inspect_system("port", 8080)["summary"])
    print("只读命令:", run_powershell("Get-Date")["summary"])
    print("危险命令:", run_powershell("Remove-Item -Recurse C:\\Windows")["summary"])
    print("越界访问:", file_manage("list", folder=r"C:\Windows\System32")["summary"])
