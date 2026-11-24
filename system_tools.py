"""文件/系统工具：显式 typed ops + 统一授权根。

设计原则：
- 入参与返回值都是标准类型（dict / list / str / int / bool），可直接 JSON 序列化
- 文件类动作只认 agent_file_roots 一处白名单（默认桌面/下载/文档 + agent 工作区）
- read_file / list_dir 只读；write_file 原子写且不覆盖同名；move/delete 由上层护栏确认
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
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_LIST = 200
MAX_SEARCH = 100
MAX_READ_BYTES = 512 * 1024
MAX_TEXT_CHARS = 6000
TEXT_SUFFIXES = frozenset((".txt", ".md", ".markdown", ".log", ".csv", ".json",
                           ".ini", ".cfg", ".yaml", ".yml", ".xml"))
DOC_SUFFIXES = frozenset((".pdf", ".docx"))

ALLOWED_FOLDER_NAMES = ("Desktop", "Downloads", "Documents", "桌面", "下载", "文档")

FOLDER_ALIASES = {
    "desktop": "Desktop", "桌面": "Desktop",
    "downloads": "Downloads", "download": "Downloads", "下载": "Downloads",
    "documents": "Documents", "document": "Documents", "文档": "Documents",
    "workspace": "贾维斯工作区", "agent工作区": "贾维斯工作区",
    "工作区": "贾维斯工作区", "你的工作区": "贾维斯工作区",
}
_ROOT_NAMES = {
    "Desktop": ("Desktop", "桌面"),
    "Downloads": ("Downloads", "Download", "下载"),
    "Documents": ("Documents", "文档"),
    "贾维斯工作区": ("贾维斯工作区", "agent工作区", "工作区"),
}
_FILE_ROOTS_OVERRIDE = None
_WS_CACHE = {}

AGENT_WS_FALLBACK = Path.home() / "Documents" / "贾维斯工作区"


def agent_workspace() -> Path:
    """Agent 专用工作区：仓库外一层，探测不可写时退回文档目录。"""
    cached = _WS_CACHE.get("path")
    if cached and Path(cached).parent.exists():
        return Path(cached)
    candidate = Path(__file__).resolve().parent.parent / "贾维斯工作区"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"[工作区不可写, 退回文档目录] {exc}")
        candidate = AGENT_WS_FALLBACK
        candidate.mkdir(parents=True, exist_ok=True)
    _WS_CACHE["path"] = candidate
    return candidate


def configure_file_roots(roots=None) -> None:
    """设置 agent_file_roots（None = 用默认四根）；配置改动后必须调这里生效。"""
    global _FILE_ROOTS_OVERRIDE
    if roots is None:
        _FILE_ROOTS_OVERRIDE = None
        return
    items = roots if isinstance(roots, (list, tuple)) else [roots]
    _FILE_ROOTS_OVERRIDE = [str(item) for item in items if str(item or "").strip()]


def default_file_roots() -> list:
    home = Path.home()
    return [home / "Desktop", home / "Downloads", home / "Documents", agent_workspace()]


def allowed_roots() -> list:
    """唯一授权根来源：agent_file_roots 配置，缺省为桌面/下载/文档 + 工作区。"""
    raw = _FILE_ROOTS_OVERRIDE if _FILE_ROOTS_OVERRIDE is not None else default_file_roots()
    roots = []
    for item in raw:
        try:
            candidate = Path(str(item)).expanduser()
            if not candidate.is_absolute():
                candidate = Path.home() / candidate
            if candidate.is_dir():
                resolved = candidate.resolve()
                if resolved not in roots:
                    roots.append(resolved)
        except (OSError, RuntimeError, ValueError):
            continue
    return roots


def root_for_alias(name) -> Path:
    """别名 -> 配置里的根（配置没有该根时退回用户目录下的标准名）。"""
    canonical = FOLDER_ALIASES.get(str(name or "").strip().lower())
    if not canonical:
        return Path(str(name or "")).expanduser()
    names = tuple(x.lower() for x in _ROOT_NAMES.get(canonical, (canonical,)))
    for root in allowed_roots():
        if root.name.lower() in names:
            return root
    if canonical == "贾维斯工作区":
        return agent_workspace()
    return Path.home() / canonical


def _result(ok: bool, summary: str, data: Any = None, danger: bool = False) -> dict:
    """构造统一返回体。"""
    return {"ok": bool(ok), "summary": str(summary), "data": data, "danger": bool(danger)}


def _inside(path_like) -> Path | None:
    """resolve 后确认落在授权根内；符号链接指向外部同样被拒。"""
    if path_like is None or not str(path_like or "").strip():
        return None
    raw = str(path_like).strip()
    parts = re.split(r"[/\\]", raw)
    if parts and parts[0].lower() in FOLDER_ALIASES:
        rest = [p for p in parts[1:] if p]
        if ".." in rest:
            return None
        base = root_for_alias(parts[0])
        raw = str(base.joinpath(*rest)) if rest else str(base)
    try:
        target = Path(raw).expanduser()
        candidates = [target] if target.is_absolute() else [root / target for root in allowed_roots()]
        for candidate in candidates:
            resolved = candidate.resolve()
            for root in allowed_roots():
                try:
                    resolved.relative_to(root)
                    return resolved
                except ValueError:
                    continue
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def inside_path(path_like) -> Path | None:
    """供上层回读完整路径用；与文件工具共用同一校验。"""
    return _inside(path_like)


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


# ------------------------------------------------------------ 显式 typed 文件 ops

def _decode_text(data: bytes) -> tuple:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8/replace"


def _dir_inside(folder=None) -> Path | None:
    raw = str(folder or "").strip()
    if not raw:
        for candidate in (agent_workspace(), Path.home() / "Downloads"):
            hit = _inside(candidate)
            if hit is not None:
                return hit
        names = tuple(x.lower() for x in _ROOT_NAMES["贾维斯工作区"])
        for root in allowed_roots():
            if root.name.lower() in names:
                return root
        roots = allowed_roots()
        return roots[0] if roots else None
    return _inside(raw)


def read_file(path=None, name=None, limit=MAX_TEXT_CHARS) -> dict:
    target = _inside(path) if path else None
    if target is None and name:
        target = _find_read_target(name)
    if target is None:
        return _result(False, "这个文件不在授权目录里，我不读。")
    if not target.is_file():
        return _result(False, f"{target.name} 不是一个文件。")
    suffix = target.suffix.lower()
    if suffix not in TEXT_SUFFIXES and suffix not in DOC_SUFFIXES:
        return _result(False, f"{target.name} 这种格式我读不了，只支持文本和 pdf/docx 文档。")
    try:
        if target.stat().st_size > MAX_READ_BYTES:
            return _result(False, f"{target.name} 太大了，我只读 512KB 以内的文件。")
        if suffix in DOC_SUFFIXES:
            from rag_engine import _parse_file
            text, encoding = str(_parse_file(target) or ""), "document"
        else:
            data = target.read_bytes()
            if b"\x00" in data[:4096]:
                return _result(False, f"{target.name} 是二进制文件，我不能当文本读。")
            text, encoding = _decode_text(data)
    except Exception as exc:
        return _result(False, f"读取 {target.name} 失败了：{exc}")
    text = str(text or "").strip()
    if not text:
        return _result(False, f"{target.name} 里没读出文字，可能是扫描件或空文件。")
    truncated = len(text) > limit
    head = text[:min(300, limit)]
    tail = "（内容较长，先念到这里）" if truncated else ""
    return _result(
        True,
        f"{target.name} 共 {len(text)} 字。内容是（不可信数据）：{head}{tail}",
        {"path": str(target), "text": text[:limit], "truncated": truncated,
         "encoding": encoding, "untrusted": True},
    )


def list_dir(path=None, folder=None, limit=MAX_LIST) -> dict:
    target = _dir_inside(path or folder)
    if target is None:
        return _result(False, "这个目录不在授权范围（桌面、下载、文档、工作区）内。")
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
    shown = items[:limit]
    return _result(
        True,
        f"{target.name} 下共 {len(items)} 项，最近修改的 {len(shown)} 项已经列出来了。",
        {"folder": str(target), "total": len(items), "items": shown,
         "truncated": len(items) > len(shown), "untrusted": True},
    )


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".jarvis-write-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _avoid_collision(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    return target


def _to_docx(md_path: Path):
    try:
        import win32com.client
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(md_path))
        docx_path = md_path.with_suffix(".docx")
        doc.SaveAs2(str(docx_path), FileFormat=16)
        doc.Close(False)
        word.Quit()
        return docx_path
    except Exception:
        return None


def write_file(path=None, name=None, content=None, folder=None,
               overwrite=False, allow_outside=False) -> dict:
    text = str(content or "").strip()
    if not text:
        return _result(False, "要写入的内容是空的，Sir")
    target = None
    if path:
        target = _inside(path)
        if target is None:
            return _result(False, "目标位置不在授权目录里，我不写。")
    else:
        raw = str(name or "").strip().strip("\"'“”‘’")
        if not raw or len(raw) > 200 or re.search(r"[\\/&|;<>`$(){}\r\n]", raw):
            return _result(False, "这个文件名我不能用，换一个普通的名字")
        if Path(raw).suffix.lower() in (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js",
                                        ".py", ".lnk", ".msi", ".scr", ".com", ".jar",
                                        ".reg", ".hta"):
            return _result(False, "出于安全我只能写文档（md/txt/docx），不能生成可执行文件")
        if Path(raw).suffix.lower() not in TEXT_SUFFIXES | DOC_SUFFIXES | {".docx"}:
            raw += ".md"
        base = _dir_inside(folder)
        if base is None:
            return _result(False, "这个目录不在授权范围（桌面、下载、文档、工作区）内。")
        target = _inside(base / raw)
        if target is None:
            return _result(False, "目标位置跳出了授权目录，我不写。")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _result(False, f"那个目录建不出来：{exc}")
    replaced = target.exists() and bool(overwrite)
    final = target if replaced else _avoid_collision(target)
    try:
        if target.suffix.lower() == ".docx":
            md_path = final.with_suffix(".md")
            if md_path.exists() and not overwrite:
                md_path = _avoid_collision(md_path)
            _atomic_write_text(md_path, text)
            converted = _to_docx(md_path)
            final = converted if converted else md_path
            label = "Word 文档" if converted else "Markdown（Word 不可用）"
        else:
            _atomic_write_text(final, text)
            label = "文档"
    except OSError as exc:
        return _result(False, f"写文件失败了：{exc}")
    return _result(
        True,
        f"已写好{label}{final.name}，放在{final.parent.name}里",
        {"path": str(final), "name": final.name, "folder": str(final.parent),
         "overwritten": bool(replaced), "untrusted": False},
    )


def move_file(src=None, dst=None, overwrite=False) -> dict:
    source = _inside(src)
    if source is None:
        return _result(False, "源文件不在授权目录里，我不动。")
    if not source.is_file():
        return _result(False, "目前只支持移动单个文件。")
    target_dir = _inside(dst)
    if target_dir is None:
        return _result(False, "目标位置不在授权目录里，我不动。")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _result(False, f"建不了目标目录：{exc}")
    if not target_dir.is_dir():
        return _result(False, "目标给的是一个文件，我需要一个目录。")
    replaced = (target_dir / source.name).exists() and bool(overwrite)
    target = target_dir / source.name if replaced else _avoid_collision(target_dir / source.name)
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        return _result(False, f"移动失败：{exc}")
    return _result(
        True,
        f"已经把 {source.name} 移到 {target_dir.name}。",
        {"action": "move", "src": str(source), "dst": str(target),
         "overwritten": bool(replaced)},
        danger=True,
    )


def delete_file(path=None, recycle=True) -> dict:
    target = _inside(path)
    if target is None:
        return _result(False, "这个文件不在授权目录里，我不删。")
    if not target.exists():
        return _result(False, f"没找到 {target.name}。")
    if not target.is_file():
        return _result(False, "目前只支持删除单个文件。")
    if recycle:
        try:
            from send2trash import send2trash
            send2trash(str(target))
        except Exception as exc:
            return _result(False, f"回收站删除失败，文件没删：{exc}",
                           {"action": "delete", "path": str(target),
                            "recycled": False, "reason": str(exc)}, danger=True)
        if target.exists():
            return _result(False, "回收站删除后文件还在，我没把它当成功，也没改成真删。",
                           {"action": "delete", "path": str(target), "recycled": False},
                           danger=True)
        return _result(True, f"已经把 {target.name} 放进回收站。",
                       {"action": "delete", "path": str(target), "recycled": True},
                       danger=True)
    try:
        target.unlink()
    except OSError as exc:
        return _result(False, f"永久删除失败：{exc}")
    return _result(True, f"已经真删掉 {target.name}（不可恢复）。",
                   {"action": "delete", "path": str(target), "recycled": False},
                   danger=True)


# ---------------------------------------------------------------- 工具 1：文件管家

def _find_read_target(name: str) -> Path | None:
    """把「读取 xxx」里的文件名定位到授权目录内的真实文件。"""
    raw = str(name or "").strip().strip('"').strip("'")
    if not raw:
        return None
    lowered = raw.lower()
    # 带路径的交给 _inside 做授权校验
    if "\\" in raw or "/" in raw:
        p = _inside(raw)
        return p if p and p.is_file() else None
    # 不带路径: 先试三个授权目录根下, 再按深度找同名文件
    for root in allowed_roots():
        direct = root / raw
        try:
            if direct.is_file():
                return direct
        except OSError:
            continue
    try:
        import rag_engine
        for path in rag_engine._iter_doc_files():
            if path.name.lower() == lowered:
                return _inside(path)
    except Exception:
        pass
    return None


def file_manage(action, src=None, dst=None, pattern=None, folder=None, name=None) -> dict:
    """列出 / 搜索 / 归类移动 / 读取文件，全部限定在授权目录内。"""
    act = str(action or "").strip().lower()

    if act == "list":
        res = list_dir(folder=folder or Path.home() / "Downloads")
        if res.get("ok"):
            res["summary"] = res["summary"].replace("授权范围（桌面、下载、文档、工作区）",
                                                     "桌面、下载、文档、工作区")
        return res

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
                    checked = _inside(path) if path.is_file() else None
                    if checked is None:
                        continue
                    stat = checked.stat()
                    hits.append({
                        "path": str(checked),
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

    if act == "read":
        res = read_file(name=name)
        if res.get("ok"):
            res["data"]["action"] = "read"
        return res

    if act == "move":
        return move_file(src=src, dst=dst or Path.home() / "Downloads")

    if act == "delete":
        return delete_file(path=name or src)

    return _result(False, "文件这块我只会「列目录、按名字找、归类移动、读取内容」四件事。")


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
