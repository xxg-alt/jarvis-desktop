"""回归: "以后截图放到另一个文件夹" 这类改设置请求。

真实事故 (jarvis-gui.log 2026-11-22):
    听到: 能不能放到你的工作区内
    贾维斯: 已写好并打开自我介绍.md
原因有两层:
  ① 系统根本没有"改截图目录"这个能力, 模型只能拿最像的动作(write_file)硬凑,
     又因为记忆里全是自我介绍, 就编了一份自我介绍文档;
  ② 这句话是"能不能…"的礼貌请求, 问句闸门会把它推给模型。

修法: 新增 set_screenshot_dir 动作 + 配置项 screenshot_dir;
     礼貌请求式命令不按问句处理; 目标含糊时必须反问, 不许猜一个目录乱建。
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import jarvis   # noqa: E402


def act(plan):
    return (plan or {}).get("action")


def main():
    fails = []

    # ── 一、分流: 这类请求必须走 set_screenshot_dir, 不能变成截图/write_file ──
    routing = [
        ("能不能把这张截图以后放到另一个文件夹里面", "set_screenshot_dir"),
        ("以后截图都放到工作区", "set_screenshot_dir"),
        ("把截图存到 D:\\shots", "set_screenshot_dir"),
        ("能不能放到你的工作区内", "set_screenshot_dir"),
        ("不要把截图放到桌面", "chat"),          # 否定 → 只回话
        ("截图", "screenshot"),                  # 正常截图没被规则抢走
        ("你截图的文件放到哪个文件夹里？", "chat"),  # 问现状 → 照实答
    ]
    for text, want in routing:
        got = act(jarvis.parse_command(text))
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  分流 {text!r:36} → {got} (期望 {want})")
        if not ok:
            fails.append(("分流", text, got, want))

    # ── 二、执行: 含糊目标必须反问, 且不碰配置文件 ──
    old_cfg_file = jarvis.USER_CFG_FILE
    old_dir = jarvis.CONFIG.get("screenshot_dir", "")
    tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_cfg_"))
    try:
        jarvis.USER_CFG_FILE = tmpdir / "config.json"

        r = jarvis._perform_plan({"action": "set_screenshot_dir",
                                  "args": {"dir": "另一个什么文件夹"}})
        say = str(r or "")
        ok = ("哪个具体目录" in say or "哪个" in say) and not jarvis.USER_CFG_FILE.exists()
        print(f"{'PASS' if ok else 'FAIL'}  含糊目标 → 反问: {say!r}")
        if not ok:
            fails.append(("含糊目标反问", say, jarvis.USER_CFG_FILE.exists()))

        r = jarvis._perform_plan({"action": "set_screenshot_dir",
                                  "args": {"dir": "工作区"}})
        say = str(r or "")
        ws = jarvis.agent_workspace()
        saved = jarvis.USER_CFG_FILE.exists() and json.loads(
            jarvis.USER_CFG_FILE.read_text(encoding="utf-8")).get("screenshot_dir")
        ok = (jarvis.CONFIG.get("screenshot_dir") == str(ws)
              and saved == str(ws) and str(ws) in say
              and jarvis.screenshot_dir() == ws)
        print(f"{'PASS' if ok else 'FAIL'}  明确目标 → 生效: cfg={jarvis.CONFIG.get('screenshot_dir')} 落盘={saved}")
        if not ok:
            fails.append(("明确目标生效", say, jarvis.CONFIG.get("screenshot_dir"), saved))

        # 改回去, 别污染真实配置
        jarvis.CONFIG["screenshot_dir"] = old_dir
    finally:
        jarvis.USER_CFG_FILE = old_cfg_file
        jarvis.CONFIG["screenshot_dir"] = old_dir

    print()
    if fails:
        print(f"失败 {len(fails)} 条")
        for f in fails:
            print("  ", f)
        return 1
    print(f"全部通过 ({len(routing)} 条分流 + 2 条执行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
