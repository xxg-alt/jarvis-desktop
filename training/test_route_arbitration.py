"""规则仲裁回归: 关键词命中 ≠ 可以动手。

守护两件事:
  ① 否定 —— "别关机"/"不要截图" 绝不能反过来执行 (这是会误伤电脑的一类)
  ② 问句 —— "你截图的文件放到哪个文件夹里?" 要回答问题, 而不是又截一张图

判定标准写在每条用例的 expect 里, 不通过就退出码 1。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis   # noqa: E402


def act(plan):
    return (plan or {}).get("action")


def say(plan):
    return str((plan or {}).get("say") or "")


CASES = [
    # (输入, 期望动作, 期望 say 含, 说明)
    ("你截图的文件放到哪个文件夹里？", "chat", "贾维斯截图", "问句 → 照实答目录, 不该再截图"),
    ("截图", "screenshot", "", "裸命令照旧执行"),
    ("帮我截个图", "screenshot", "", "带修饰的命令照旧执行"),
    ("别截图了", "chat", "不截图", "否定 → 不执行"),
    ("不要截图", "chat", "不截图", "否定 → 不执行"),
    ("关机", "shutdown", "", "高危命令照旧(执行侧还要确认)"),
    ("不要关机", "chat", "不关机", "否定 → 不关机"),
    ("别关机，我还在下载", "chat", "不关机", "带从句的否定"),
    ("关机了吗", None, "", "疑问 → 不能当命令执行"),
    ("音量调到37%", "volume", "", "精确参数仍走规则"),
    ("音量调大", "volume", "", "方向调节仍走规则"),
    ("打开记事本", "open_app", "", "高频短指令必须零延迟"),
    ("别打开微信", "chat", "不打开", "否定 → 不打开"),
    ("能同时打开两个应用吗", None, "", "疑问 → 让位给模型(旧版本误判为 search_file)"),
    ("搜索明天天气", "web_search", "", "以动词开头 = 命令"),
    ("搜索一下明天天气怎么样", "web_search", "", "句中有疑问词但以'搜索'开头, 仍是命令"),
    ("打开什么文件夹", None, "", "问句 → 不给 open_* 动作"),
    ("锁屏", "lock", "", "照旧"),
    ("别锁屏", "chat", "不锁屏", "否定 → 不锁屏"),
]

def main():
    fails = []
    for text, want_act, want_say, note in CASES:
        plan = jarvis.parse_command(text)
        got_act = act(plan)
        got_say = say(plan)
        ok = True
        if want_act is None:
            # 让位给模型: 允许返回 None 或 chat, 但不许给出任何有副作用的动作
            if got_act not in (None, "chat"):
                ok = False
        else:
            if got_act != want_act:
                ok = False
        if want_say and want_say not in got_say:
            ok = False
        print(f"{'PASS' if ok else 'FAIL'}  {text!r:28} → {got_act}  {note}")
        if not ok:
            fails.append((text, want_act, got_act, got_say, note))

    # 额外: 否定句必须一个副作用动作都没有
    no_side = ["别截图了", "不要关机", "别打开微信", "别锁屏"]
    for text in no_side:
        plan = jarvis.parse_command(text)
        if act(plan) != "chat":
            fails.append((text, "chat", act(plan), say(plan), "否定句仍产生了动作"))

    print()
    if fails:
        print(f"失败 {len(fails)} 条")
        for f in fails:
            print("  ", f)
        return 1
    print(f"全部通过 ({len(CASES)} 条 + 否定副作用检查 {len(no_side)} 条)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
