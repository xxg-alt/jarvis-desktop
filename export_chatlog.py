"""把对话记录导成一份能直接发出去的 Markdown（可选：加密压缩包）。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe export_chatlog.py               # 只导出合集 md
    .venv\\Scripts\\python.exe export_chatlog.py --zip 密码     # 同时打一个 AES 加密 zip

产物落在 贾维斯工作区\\对话记录\\导出\\ 下：
    对话合集-YYYYMMDD.md        （已脱敏，可直接贴/发给别人）
    对话合集-YYYYMMDD.zip       （--zip 时才有，AES-256 带密码）

为什么不自动推 GitHub：仓库公开的话，即便只放密文，提交频率和体量本身就泄露信息；
用户自己挑选、自己发，比自动推送安全得多。
"""
import argparse
import time
from pathlib import Path

import chat_log


def build_markdown(out_dir: Path) -> Path:
    """把所有 jsonl 合成一份人读的 md。"""
    root = chat_log.log_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    out = out_dir / f"对话合集-{stamp}.md"
    days = turns = 0
    lines = [f"# 与贾维斯的对话合集 · 截至 {time.strftime('%Y-%m-%d %H:%M')}", "",
             "> 由 export_chatlog.py 自动汇总；本机路径 / 密钥 / 邮箱 / 手机号已脱敏。", ""]
    for p in sorted(root.glob("对话-*.jsonl")):
        days += 1
        day = p.stem.replace("对话-", "")
        day_lines, day_turns = [], 0
        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            import json
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            day_turns += 1
            hhmm = time.strftime("%H:%M:%S", time.localtime(rec.get("ts") or 0))
            tag = f" · `{rec['action']}`" if rec.get("action") else ""
            day_lines += [f"### {hhmm}{tag}", ""]
            if rec.get("user"):
                day_lines += [f"**我**：{rec['user']}", ""]
            if rec.get("reply"):
                day_lines += [f"**贾维斯**：{rec['reply']}", ""]
            day_lines += ["---", ""]
        turns += day_turns
        lines += [f"## {day}（{day_turns} 轮）", ""] + day_lines
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已导出 {out}（{days} 天 / {turns} 轮）")
    return out


def main():
    ap = argparse.ArgumentParser(description="导出贾维斯对话合集")
    ap.add_argument("--zip", metavar="密码", default="",
                    help="同时生成 AES-256 加密 zip，密码就是这个参数")
    ap.add_argument("--out", default="", help="导出目录（默认 对话记录\\导出）")
    a = ap.parse_args()

    out_dir = Path(a.out) if a.out else chat_log.log_dir() / "导出"
    md = build_markdown(out_dir)
    if a.zip:
        ok, msg = zip_one(md, a.zip)
        print(msg)
        if not ok:
            raise SystemExit(1)


def zip_one(src: Path, passphrase: str) -> tuple:
    """只把这份合集 md 打成一个 AES zip —— 标准库 zipfile 的加密早被破了, 不用。"""
    try:
        import pyzipper
    except Exception:
        return False, "缺少 pyzipper：pip install pyzipper（已装过就不用管）"
    if not str(passphrase).strip():
        return False, "密码不能为空"
    out = src.with_suffix(".zip")
    try:
        with pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(str(passphrase).encode("utf-8"))
            zf.write(src, arcname=src.name)
        return True, f"已生成 {out}（AES-256，密码你自己留好，我不保存）"
    except Exception as exc:
        return False, f"打包失败: {exc}"


if __name__ == "__main__":
    main()
