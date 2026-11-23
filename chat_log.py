"""对话留档: 每轮对话追加到本地文件 (机器读 + 人读两份视图)。

为什么不能只靠 memory.json:
    它是**滚动窗口** (_MAX_CONVERSATION 上限), 聊够了就丢最早的 —— 打开一看正好 240 条。
    "每一次都留下" 必须是独立、append-only 的文件, 不跟记忆混在一起。

两份视图, 一份真相:
    对话记录/对话-YYYY-MM-DD.jsonl   机器读 (训练/统计/导出都用它)
    对话记录/对话-YYYY-MM-DD.md      人读   (GitHub、笔记软件里直接看)

隐私: 写盘前统一过 redact() —— 家目录路径变 %USERPROFILE%(其它盘符的绝对路径同样),
      密钥/邮箱/手机号/base64 长串打码。只写本地, 不联网, 不推送。

加密: 本机保存的是明文(要你自己看, 也要能拿去训练);
      往外发的包另做, 见 export_encrypted_zip() —— 需要 pyzipper(AES zip) 才能带密码。
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

_SESSION_ID = uuid.uuid4().hex[:8]          # 一次运行一个, 便于把连续几轮归成一段会话

# ── 脱敏 ──
_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'，。；]+")
_OTHER_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"'，。；]+\\?){1,6}")
_API_KEY_RE = re.compile(r"(?:sk|ak|pk|ghp|github_pat)[-_][A-Za-z0-9_\-]{8,}")
_BEARER_RE = re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._\-]+")
_MAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_B64_RE = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")


def redact(text) -> str:
    """把会泄露本机/账号信息的片段打码, 其余原样保留。"""
    s = str(text or "")
    if not s:
        return ""
    s = _USER_PATH_RE.sub("%USERPROFILE%", s)
    s = _OTHER_PATH_RE.sub(lambda m: m.group(0), s)       # 其它盘符路径保留(用户自己要看)
    s = _API_KEY_RE.sub(lambda m: m.group(0)[:3] + "***", s)
    s = _BEARER_RE.sub(r"\1 ***", s)
    s = _MAIL_RE.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[1], s)
    s = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], s)
    s = _B64_RE.sub("[base64 已略]", s)
    return s


def log_dir() -> Path:
    """日志目录: 环境变量优先, 否则项目根下的 贾维斯工作区/对话记录。"""
    env = os.environ.get("JARVIS_CHATLOG_DIR", "").strip()
    if env:
        return Path(env)
    base = Path(__file__).resolve().parent
    for cand in (base.parent / "贾维斯工作区" / "对话记录",
                 Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents"
                 / "贾维斯工作区" / "对话记录"):
        try:
            probe = cand / ".write_probe"
            cand.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return cand
        except Exception:
            continue
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "对话记录"


def _day_paths(ts: float):
    d = time.strftime("%Y-%m-%d", time.localtime(ts))
    root = log_dir()
    return root / f"对话-{d}.jsonl", root / f"对话-{d}.md"


_MD_HEADER = "# 与贾维斯的对话 · {day}\n\n> 由贾维斯自动追加，只记录对话本身；路径与密钥已脱敏。\n\n"


def log_turn(user: str, reply: str, action: str = "", args=None,
             source: str = "", ts: float = None, extra: dict = None,
             kind: str = "") -> dict:
    """追加一轮对话。返回写下的记录 (失败返回 {})。

    参数全部可选容错: 调用方在异常路径上也会调, 这里不能抛, 更不能把主流程带崩。
    """
    ts = float(ts or time.time())
    u = redact(user)
    r = redact(reply)
    if not u and not r:
        return {}
    rec = {
        "ts": round(ts, 3),
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
        "session": _SESSION_ID,
        "user": u,
        "reply": r,
        "action": str(action or ""),
        "args": redact(json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args),
        "source": str(source or ""),
        "kind": str(kind or ""),
    }
    if extra:
        rec["extra"] = extra

    jsonl_path, md_path = _day_paths(ts)
    try:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[对话留档] JSONL 写入失败: {exc}")
        return {}

    try:
        new_file = not md_path.exists()
        with open(md_path, "a", encoding="utf-8", newline="\n") as f:
            if new_file:
                f.write(_MD_HEADER.format(day=time.strftime("%Y-%m-%d", time.localtime(ts))))
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            tag = f" · `{rec['action']}`" if rec["action"] else ""
            src = f" · {rec['source']}" if rec["source"] else ""
            f.write(f"### {stamp}{tag}{src}\n\n")
            if rec["user"]:
                f.write(f"**我**：{rec['user']}\n\n")
            if rec["reply"]:
                f.write(f"**贾维斯**：{rec['reply']}\n\n")
            f.write("---\n\n")
    except Exception as exc:
        print(f"[对话留档] Markdown 写入失败: {exc}")
    return rec


def stats(root: Path = None) -> dict:
    """粗略统计: 天数、轮数、字节数。给 /训练导出/ 用。"""
    root = Path(root or log_dir())
    days, turns, size = 0, 0, 0
    for p in sorted(root.glob("对话-*.jsonl")):
        days += 1
        size += p.stat().st_size
        try:
            with open(p, encoding="utf-8") as f:
                turns += sum(1 for line in f if line.strip())
        except Exception:
            continue
    return {"days": days, "turns": turns, "bytes": size}


def export_encrypted_zip(out_path: str, passphrase: str) -> tuple:
    """把对话日志打成带密码的 AES zip (发给别人时用)。

    需要 pyzipper —— 不用 zipfile, 因为标准库的 zip 加密是早已被破的 ZipCrypto。
    返回 (成功?, 说明)。
    """
    try:
        import pyzipper
    except Exception:
        return False, "缺少 pyzipper：先执行 pip install pyzipper，才能生成带密码的压缩包"
    if not str(passphrase or "").strip():
        return False, "密码不能为空"
    root = log_dir()
    files = sorted(root.glob("对话-*"))
    if not files:
        return False, "还没有任何对话记录"
    try:
        with pyzipper.AESZipFile(out_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(str(passphrase).encode("utf-8"))
            for p in files:
                zf.write(p, arcname=p.name)
        return True, f"已生成 {out_path}（{len(files)} 个文件，AES-256 加密）"
    except Exception as exc:
        return False, f"打包失败: {exc}"
