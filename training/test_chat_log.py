"""回归: 对话留档 (chat_log) —— 每轮追加、脱敏、两份视图。

不测"有没有记", 测四件容易坏的事:
  ① 追加而非覆盖: 连续两轮后 jsonl 必须 2 行、md 必须包含两段
  ② 脱敏: 家目录路径变 %USERPROFILE%, 密钥/邮箱/手机号打码
  ③ 两份视图同步: jsonl 有的轮次 md 也要有
  ④ 不污染主流程: 目录写不进去时也只能返回空, 不许抛
"""
import json
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import chat_log   # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all = ok_all and ok
    print(f"{'PASS' if ok else 'FAIL'}  {name} {detail}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="chatlog_"))
    os.environ["JARVIS_CHATLOG_DIR"] = str(tmp)

    # ① 两轮追加
    a = chat_log.log_turn("打开记事本", "这就为您打开记事本，Sir", action="open_app",
                          args={"exe": "notepad.exe"}, source="local", ts=1780000000)
    b = chat_log.log_turn("看看屏幕上这个是什么", "屏幕上是一个报错弹窗，写着 ERROR 0x80070005",
                          action="peek_screen", args={"question": "这是什么"}, source="llm",
                          ts=1780000060)
    jsonl = Path(chat_log.log_dir()) / "对话-2026-05-28.jsonl"
    if not jsonl.exists():
        # 时区差异兜底: 直接找当天那份
        jsonl = sorted(Path(chat_log.log_dir()).glob("对话-*.jsonl"))[0]
    lines = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("追加而非覆盖", len(lines) == 2, f"行数={len(lines)}")
    check("会话号一致", lines[0]["session"] == lines[1]["session"])
    check("字段齐全", all(k in lines[0] for k in
                         ("ts", "time", "session", "user", "reply", "action", "source")))

    md = jsonl.with_suffix(".md")
    text = md.read_text(encoding="utf-8")
    check("md 有两段", text.count("**我**：") == 2 and "ERROR 0x80070005" in text)
    check("md 带动作标签", "`open_app`" in text and "`peek_screen`" in text)

    # ② 脱敏
    dirty = (r"把 C:\Users\Ljj13\Desktop\a.txt 打开, 我的 key 是 sk-abcdef123456789 "
             "邮箱 ljj1314@example.com 手机 13812345678")
    red = chat_log.redact(dirty)
    check("路径脱敏", "%USERPROFILE%\\Desktop\\a.txt" in red and "Ljj13" not in red, red)
    check("密钥脱敏", "abcdef123456789" not in red and "sk-***" in red)
    check("邮箱脱敏", "1314@example.com" not in red, red)
    check("手机脱敏", "13812345678" not in red and "138****5678" in red, red)

    # 写进日志后, 落盘的文件里也不能有原始敏感串
    chat_log.log_turn(dirty, "好的", action="chat", ts=1780000120)
    raw = jsonl.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    check("落盘文件已脱敏", "Ljj13" not in raw and "abcdef123456789" not in raw)

    # ④ 目录不可写时不抛
    os.environ["JARVIS_CHATLOG_DIR"] = "Z:\\不存在的盘\\logs"
    try:
        got = chat_log.log_turn("随便说一句", "随便回一句")
        check("坏目录不抛异常", got == {} or isinstance(got, dict))
    except Exception as exc:
        check("坏目录不抛异常", False, str(exc))

    os.environ.pop("JARVIS_CHATLOG_DIR", None)
    st = chat_log.stats(tmp)
    print(f"\n统计: {st}")
    check("统计能读", st["turns"] >= 2 and st["days"] >= 1)

    print("\n结果:", "全部通过" if ok_all else "有失败")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
