# -*- coding: utf-8 -*-
"""
贾维斯智能软件寻路与启动引擎 (Smart App Launcher)
解决传统"写死 WeChat.exe 导致报错"的死板问题。

多级寻径机制:
1. 【直达扫描】: 开始菜单与桌面全部快捷方式 (.lnk) 智能匹配与解析
2. 【注册表 App Paths】: 查询 Windows 系统注册表已注册程序
3. 【常见磁盘扫描】: 动态探查主流安装目录 (D:\, C:\Program Files, AppData 等)
4. 【智能兜底】: 如未能直接打开，返回友好智能的自然语言解释，而不是冷冰冰一句"已打开微信"
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    import winreg
except ImportError:  # 非 Windows 环境仅用于静态检查
    winreg = None

# 缓存已找到的真实软件路径，避免重复扫盘
APP_PATH_CACHE = {}

# 只接受"裸程序名": 字母/数字/下划线/点/横线, 最多 64 字。
# 关键: 这里不放行空格、& | ; > < " ' % ^ 、以及任何 :// 前缀和路径分隔符,
# 所以模型即便返回 "calc.exe & powershell -enc ..." 也会被判为非法, 根本不会被启动。
_PROGRAM_NAME_RE = re.compile(r"^[\w.\-]{1,64}$")

def get_shortcut_target(lnk_path: str) -> str:
    """解析 .lnk 快捷方式的真实目标 exe 路径"""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(lnk_path))
        target = shortcut.TargetPath.strip()
        if target and os.path.exists(target):
            return target
    except Exception:
        pass
    return ""

def scan_start_menu_and_desktop(app_name: str) -> str:
    """在开始菜单和桌面上智能搜寻同名快捷方式"""
    name_clean = app_name.lower().replace(" ", "").replace(".exe", "")
    
    search_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        r"C:\Users\Public\Desktop"
    ]
    
    # 关键词同义词扩展
    synonyms = [name_clean]
    if name_clean in ("微信", "wechat", "weixin"):
        synonyms.extend(["微信", "wechat", "weixin"])
    elif name_clean in ("网易云", "网易云音乐", "cloudmusic"):
        synonyms.extend(["网易云音乐", "cloudmusic"])
    elif name_clean in ("qq", "腾讯qq"):
        synonyms.extend(["qq", "tencent"])

    for d in search_dirs:
        p = Path(d)
        if not p.exists():
            continue
        try:
            for lnk in p.rglob("*.lnk"):
                fname = lnk.stem.lower()
                for syn in synonyms:
                    if syn in fname and "卸载" not in fname and "uninstall" not in fname:
                        real_target = get_shortcut_target(str(lnk))
                        if real_target and real_target.lower().endswith(".exe"):
                            return real_target
                        # 如果是 UWP 或特殊快捷方式，直接返回 lnk 路径也能被 startfile 执行
                        return str(lnk)
        except Exception:
            continue

    return ""

def scan_common_drives(app_name: str) -> str:
    """常见非系统盘和路径智能嗅探 (例如 D:\微信文件夹\Weixin\Weixin.exe)"""
    name_clean = app_name.lower().replace(" ", "").replace(".exe", "")
    
    candidates = []
    if name_clean in ("微信", "wechat", "weixin"):
        candidates = [
            r"D:\微信文件夹\Weixin\Weixin.exe",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
            r"D:\Tencent\WeChat\WeChat.exe",
            r"D:\WeChat\WeChat.exe"
        ]
    elif name_clean in ("steam", "steam游戏"):
        candidates = [
            r"C:\Program Files (x86)\Steam\steam.exe",
            r"D:\Steam\steam.exe",
            r"E:\Steam\steam.exe"
        ]
    
    for c in candidates:
        if os.path.exists(c):
            return c
            
    return ""

def find_app_paths_exe(exe_name: str) -> str:
    """查注册表 App Paths —— Windows 官方登记的程序路径 (微信/Chrome/Steam 等都在这)"""
    if winreg is None or not _PROGRAM_NAME_RE.match(exe_name):
        return ""
    sub = "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as key:
                path, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        path = str(path or "").strip().strip('"')
        if path.lower().endswith(".exe") and os.path.isfile(path):
            return path
    return ""


def resolve_program(target: str) -> str:
    """把"程序名"解析成真实 .exe 路径; 解析不到就返回空串。

    安全约定: 只返回存在的 .exe 绝对路径, 绝不把模型给的原始字符串交给 shell。
    所以 "calc" 能解析成 C:\\Windows\\System32\\calc.exe,
    而 "calc.exe & powershell ..." 会被判非法直接返回空串。
    """
    t = str(target or "").strip().strip('"')
    if not t or len(t) > 260:
        return ""

    # 1. 已经是存在的 .exe 绝对路径
    if os.path.isabs(t):
        return t if t.lower().endswith(".exe") and os.path.isfile(t) else ""

    # 2. 裸程序名 (不允许夹带任何 shell 语法/协议前缀/路径分隔符)
    if not _PROGRAM_NAME_RE.match(t):
        return ""
    exe = t if t.lower().endswith(".exe") else t + ".exe"

    # 3. PATH 环境变量
    found = shutil.which(exe)
    if found and found.lower().endswith(".exe"):
        return found

    # 4. 注册表 App Paths
    return find_app_paths_exe(exe)


def smart_launch_app(app_target: str, app_name: str) -> tuple[bool, str]:
    """
    智能启动应用
    返回: (是否成功, 语音回复文字)
    """
    # 1. 优先查缓存
    if app_name in APP_PATH_CACHE and os.path.exists(APP_PATH_CACHE[app_name]):
        real_exe = APP_PATH_CACHE[app_name]
        os.startfile(real_exe)
        return True, f"已为您启动{app_name}"

    # 2. 如果传入的已经是存在的绝对路径 (只接受 .exe / .lnk, 避免 startfile 执行任意脚本)
    if os.path.isabs(app_target) and os.path.exists(app_target):
        if Path(app_target).suffix.lower() in (".exe", ".lnk"):
            APP_PATH_CACHE[app_name] = app_target
            os.startfile(app_target)
            return True, f"已为您打开{app_name}"

    # 3. 在快捷方式与开始菜单嗅探
    found_path = scan_start_menu_and_desktop(app_name)
    if found_path and os.path.exists(found_path):
        APP_PATH_CACHE[app_name] = found_path
        os.startfile(found_path)
        return True, f"已找到并为您启动{app_name}"

    # 4. 在常见磁盘探查
    drive_path = scan_common_drives(app_name)
    if drive_path and os.path.exists(drive_path):
        APP_PATH_CACHE[app_name] = drive_path
        os.startfile(drive_path)
        return True, f"已在磁盘中找到并为您启动{app_name}"

    # 4b. 按中文名/别名匹配桌面与开始菜单 (config 里没写、但机器上确实装了的软件)
    try:
        from app_index import find_app
        hit = find_app(app_name or app_target)
    except Exception:
        hit = None
    if hit:
        APP_PATH_CACHE[app_name] = hit["path"]
        os.startfile(hit["path"])
        return True, f"已为您启动{hit['name']}"

    # 5. 交给 Windows 按程序名查找 (PATH / 注册表 App Paths)
    #    安全: 用列表形式启动, 且目标必须是解析出来的真实 .exe 路径。
    #    绝不使用 shell=True —— 字符串里的 & | ; 只会被当成普通字符, 不会变成"再执行一条命令"。
    resolved = resolve_program(app_target)
    if resolved:
        try:
            subprocess.Popen([resolved])
            APP_PATH_CACHE[app_name] = resolved
            return True, f"已为您启动{app_name}"
        except Exception:
            pass

    # 5b. 解析不到真实路径时, 只把"纯程序名"交给 ShellExecute (os.startfile)。
    #     它不是 shell —— 不会把 & 当成分隔符再执行下一条命令;
    #     且能走到这里的字符串已通过裸名校验 (无空格/无 :// /无路径分隔符),
    #     所以 mspaint 这类走应用别名的系统程序照样能开。
    bare = str(app_target or "").strip()
    if _PROGRAM_NAME_RE.match(bare):
        try:
            os.startfile(bare)
            return True, f"已尝试为您启动{app_name}"
        except OSError:
            pass

    return False, f"抱歉Sir，系统未能在您的电脑中定位到{app_name}的安装路径。您可以告诉我它的安装目录，我来为您配置。"
