# -*- coding: utf-8 -*-
"""训练师种子课: 把主人档案和标准操作案例写进贾维斯长期记忆。

用法: 在贾维斯电脑上, 项目目录里运行  python training/seed_training.py
写入的是 %APPDATA%/Jarvis/memory.json (和贾维斯本体同一份记忆):
- notes: 主人档案 (build_context 会按相关度召回)
- learned: 标准操作案例 (record_turn 机制, 相似指令时作示例召回)
重复运行安全: notes 按文本去重, learned 按指令去重, 只保留最新一份。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis_memory import JarvisMemory  # noqa: E402

NOTES = [
    "主人叫 ljj，是虚拟现实技术专业的大学生，正在学 Unity 和 C#。",
    "主人喜欢简短直接的回答，先说结论，不喜欢长篇大论和重复确认。",
    "主人平时用微信、Steam、网易云音乐，开发用 VS Code 和 Unity。",
    "主人说“星光”也是在叫贾维斯，两个都是唤醒词。",
]

LEARNED = [
    ("小声一点", {"action": "volume", "args": {"mode": "down"}, "say": "好的，已调低音量，Sir"}),
    ("大声一点", {"action": "volume", "args": {"mode": "up"}, "say": "好的，已调高音量，Sir"}),
    ("截个图", {"action": "screenshot", "args": {}, "say": "这就为您截图，Sir"}),
    ("电脑有点卡", {"action": "inspect_system", "args": {"mode": "top_memory"}, "say": "我看看是什么在吃内存，Sir"}),
    ("打开威信", {"action": "open_app", "args": {"exe": "WeChat.exe", "name": "微信"}, "say": "这就为您打开微信，Sir"}),
    ("打开记事版", {"action": "open_app", "args": {"exe": "notepad.exe", "name": "记事本"}, "say": "这就为您打开记事本，Sir"}),
]

CORRECTIONS = [
    ("打开计算机", "不是计算机，是计算器",
     {"action": "open_app", "args": {"exe": "calc.exe", "name": "计算器"}, "say": "这就为您打开计算器，Sir"}),
]


def main():
    memory = JarvisMemory()
    for note in NOTES:
        memory.add_note(note)
    for command, plan in LEARNED:
        memory.record_turn(command, plan, source="trainer-seed")
    for before, after, plan in CORRECTIONS:
        memory.record_correction(before, after, plan)
    stats = memory.stats()
    print(f"种子课完成: 笔记 {stats['notes']} 条, 操作案例 {stats['learned']} 个, "
          f"纠正案例 {stats['corrections']} 个")
    print(f"记忆文件: {stats['path']}")


if __name__ == "__main__":
    main()
