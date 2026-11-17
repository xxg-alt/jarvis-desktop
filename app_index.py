# -*- coding: utf-8 -*-
"""应用清单索引 (App Index)

目标: 让用户用嘴打开任何"装了、但没写进 config.json"的软件。

做法:
1. 扫描 桌面 / 公共桌面 / 两个开始菜单 里所有 .lnk, 拿到软件显示名和真实目标
2. 给每个名字生成多种"可被说出口"的键: 中文名 / 英文名 / 全拼 / 首字母
3. 查询时做多级模糊匹配: 精确 > 包含 > 全拼 > 首字母 > 近似度

这样 "打开抖音" 能命中 douyin.exe, "打开豆包" 能命中 豆包.lnk,
"打开斗包"(听错一个字) 也能靠拼音命中 豆包。
"""
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 只接受快捷方式和可执行文件, 避免把桌面上的文档/脚本当成软件启动
_LAUNCHABLE_EXT = {".lnk", ".exe", ".url"}

# 快捷方式名里的噪音: "xxx - 快捷方式"、"xxx.exe"、"(1)" 之类
_NOISE_RE = re.compile(r"\s*[-—–]\s*快捷方式\s*$|\.exe\s*$|\s*\(\d+\)\s*$", re.I)
_JUNK_RE = re.compile(r"卸载|uninstall|安装包|setup", re.I)

_INDEX = None

# 这些目录里全是工具链文件, 扫进来只会污染清单 (比如 .venv\Scripts\pip.exe)
_JUNK_DIRS = {".venv", "venv", "env", "node_modules", "__pycache__", "site-packages",
              "build", "dist", "models", ".git", "custom_voices"}


def _scan_dirs():
    """返回 (目录, 最大深度, 来源标记)"""
    dirs = [(Path.home() / "Desktop", 2, "desktop")]
    public = os.environ.get("PUBLIC")
    if public:
        dirs.append((Path(public) / "Desktop", 2, "desktop"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append((Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs", 4, "start"))
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        dirs.append((Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs", 4, "start"))
    return [d for d in dirs if d[0].exists()]


def _walk_shortcuts(root: Path, max_depth: int):
    """有限深度遍历, 避免把桌面上的项目目录 (.venv 之类) 整个扫一遍"""
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for path in children:
            try:
                if path.is_dir():
                    if depth + 1 < max_depth and path.name.lower() not in _JUNK_DIRS:
                        stack.append((path, depth + 1))
                elif path.suffix.lower() in _LAUNCHABLE_EXT and path.is_file():
                    yield path
            except OSError:
                continue


def _clean_name(stem: str) -> str:
    s = _NOISE_RE.sub("", stem).strip()
    s = re.sub(r"^启动", "", s).strip()
    return s or stem.strip()


def _pinyin(text: str) -> str:
    """整串转紧凑拼音; 英文原样保留, 只留字母数字 (Blender 4.4 -> blender44)"""
    try:
        from pypinyin import lazy_pinyin
        text = "".join(lazy_pinyin(text))
    except Exception:
        pass
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _initials(text: str) -> str:
    """首字母: 豆包 -> db, Visual Studio -> vs"""
    try:
        from pypinyin import lazy_pinyin, Style
        return re.sub(r"[^a-z0-9]", "", "".join(lazy_pinyin(text, style=Style.FIRST_LETTER)).lower())
    except Exception:
        return ""


def build_index(force: bool = False):
    """扫描并建立索引; 每个进程只扫一次 (约 0.2 秒), 结果缓存在内存里"""
    global _INDEX
    if _INDEX is not None and not force:
        return _INDEX

    entries = []
    seen_paths = set()
    for root, depth, src in _scan_dirs():
        for path in _walk_shortcuts(root, depth):
            name = _clean_name(path.stem)
            if not name or _JUNK_RE.search(name):
                continue
            key = str(path).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            entries.append({
                "name": name,
                "path": str(path),
                "src": src,
                "py": _pinyin(name),
                "ini": _initials(name),
                "low": name.lower().replace(" ", ""),
            })

    # 桌面上的快捷方式排在前面: 那是用户自己放在眼前的东西
    entries.sort(key=lambda e: (0 if e["src"] == "desktop" else 1, len(e["name"])))
    _INDEX = entries
    return _INDEX


def all_apps(desktop_only: bool = False):
    """返回去重后的软件名列表 (给"你能打开哪些软件"用)"""
    names, seen = [], set()
    for e in build_index():
        if desktop_only and e["src"] != "desktop":
            continue
        n = e["name"]
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        names.append(n)
    return names


def find_app(query: str, threshold: float = 0.78):
    """按口语名字找软件, 返回 {"name", "path", "score"} 或 None

    匹配顺序: 完全一致 > 名字包含 > 拼音包含 > 首字母 > 近似度兜底。
    阈值刻意偏严, 宁可说"没找到"也不要乱开一个不相干的软件。
    """
    q = str(query or "").strip()
    if not q:
        return None
    q = re.sub(r"^(一下|那个|我的|请)", "", q).strip()
    if not q:
        return None

    q_low = q.lower().replace(" ", "")
    q_py = _pinyin(q)
    q_ini = _initials(q)

    best = None
    for e in build_index():
        score = 0.0
        if q_low == e["low"]:
            score = 1.0
        elif q_low and (q_low in e["low"] or e["low"].startswith(q_low)):
            score = 0.92
        elif q_py and q_py == e["py"]:
            score = 0.9
        elif q_py and len(q_py) >= 2 and q_py in e["py"]:
            score = 0.86
        elif q_ini and len(q_ini) >= 2 and q_ini == e["ini"]:
            score = 0.8
        else:
            # 近似度兜底: 处理"听错一个字"的情况 (豆包 / 斗包)
            ratio = max(
                SequenceMatcher(None, q_py, e["py"]).ratio() if q_py and e["py"] else 0.0,
                SequenceMatcher(None, q_low, e["low"]).ratio(),
            )
            if ratio >= threshold:
                score = ratio

        if score and (best is None or score > best["score"]
                      or (score == best["score"] and len(e["name"]) < len(best["name"]))):
            best = {"name": e["name"], "path": e["path"], "score": round(score, 3)}
            if score >= 1.0:
                break

    return best


def suggestions(query: str, limit: int = 3):
    """没找到时给几个"你可能是想打开"的候选, 按近似度排序"""
    q = _pinyin(str(query or ""))
    if not q:
        return []
    scored = []
    for e in build_index():
        r = max(
            SequenceMatcher(None, q, e["py"]).ratio() if e["py"] else 0.0,
            SequenceMatcher(None, str(query).lower(), e["low"]).ratio(),
        )
        scored.append((r, e["name"]))
    scored.sort(reverse=True)
    out, seen = [], set()
    for r, n in scored:
        if r < 0.68 or n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
        if len(out) >= limit:
            break
    return out
