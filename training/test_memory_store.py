# -*- coding: utf-8 -*-
"""记忆自适应模块测试: 写入/上限/回滚/多命中不猜删/注入拼装。"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import memory_store  # noqa: E402

fails = []


def check(condition, message):
    if not condition:
        fails.append(message)
        print("FAIL ", message)
    else:
        print("PASS ", message)


def fresh(tmp, name="memory_store.json"):
    store = memory_store.MemoryStore(Path(tmp) / name)
    memory_store.set_store(store)
    return store


with tempfile.TemporaryDirectory() as tmp:
    store = fresh(tmp)
    result = store.add("开会前先准备笔记本", kind="fact", source="explicit")
    entry = result.get("entry") or {}
    check(result["ok"] and all(k in entry for k in
                               ("id", "text", "kind", "source", "created", "active")),
          "写入条目含 id/text/kind/source/created/active")
    check(entry.get("active") is True and entry.get("kind") == "fact", "默认 fact 且 active")
    check(store.path.exists() and store.changelog_path.exists()
          and len(store.changelog_path.read_text(encoding="utf-8").splitlines()) == 1,
          "只写自己的记忆文件与 append-only 变更日志")
    check(sorted(p.name for p in Path(tmp).iterdir()) == ["memory_store.json", "memory_store_changes.jsonl"],
          "没有写任何代码/配置/密钥文件")

    too_long = store.add("记" * 201)
    check(not too_long["ok"] and "200" in too_long["say"] and len(store.entries) == 1,
          "单条 >200 字拒绝并播报")

    for i in range(99):
        store.add(f"记忆条目{i:03d}")
    check(len(store.entries) == 100, "总数到 100 条")
    overflow = store.add("第101条")
    check(not overflow["ok"] and "100" in overflow["say"] and len(store.entries) == 100,
          "总条数 >100 拒绝并播报")

    lines_before = len(store.changelog_path.read_text(encoding="utf-8").splitlines())
    rolled = store.rollback()
    check(rolled["ok"] and len(store.entries) == 99, "回滚最近一次写入")
    lines_after = len(store.changelog_path.read_text(encoding="utf-8").splitlines())
    check(lines_after == lines_before + 1, "回滚本身也追加日志, 历史不被改写")

    store2 = fresh(tmp, "forget.json")
    store2.add("周报要写成文档")
    store2.add("周报模板放在文档里")
    ambiguous = store2.forget("周报")
    check(bool(ambiguous.get("ambiguous")) and len(store2.entries) == 2,
          "多命中不猜删, 先列出让用户选")
    check("第几条" in ambiguous["say"], "多命中播报让用户选第几条")
    one = store2.forget("", choose_id=ambiguous["entries"][0]["id"])
    check(one["ok"] and len(store2.entries) == 1, "用户选定后按 id 删除唯一命中")

    store3 = fresh(tmp, "prompt.json")
    store3.add("用户喜欢简短回答", kind="preference", source="explicit")
    store3.add("每次开会带笔记本", kind="habit", source="explicit")
    store3.add("打印机在二楼", kind="fact", source="explicit")
    store3.entries[0]["active"] = False
    inactive_text = store3.entries[0]["text"]
    block = store3.prompt_block()
    check("主人长期记忆" in block and "称呼=先生" in block, "注入标题与内置种子称呼")
    check(inactive_text not in block, "非 active 条目不注入")
    check("默认称呼" in block and "不要用 Sir/主人" in block, "注入提示明确称呼纪律")
    ordered = [line for line in block.splitlines() if line.startswith("- [")]
    kinds = [line.split("[")[1].split("]")[0] for line in ordered]
    check(len(kinds) == 2 and kinds == sorted(kinds), "按 kind 排序注入")

    for i in range(45):
        store3.add(f"补充记忆{i:03d}", kind="fact")
    block = store3.prompt_block()
    count = sum(1 for line in block.splitlines() if line.startswith("- ["))
    check(count == 40, "条目多时按 kind 排序取前 40 条")

    store4 = fresh(tmp, "title.json")
    check(store4.current_title() == "先生", "没有称呼记忆时默认先生")
    store4.set_title("老板")
    store4.set_title("先生")
    titles = [item for item in store4.entries if item["kind"] == "preference"
              and item["text"].startswith("称呼=")]
    check(len(titles) == 1 and titles[0]["text"] == "称呼=先生"
          and store4.current_title() == "先生", "同类称呼条目更新不新增")

    check(memory_store.clean_text("第一行\n第二行\t第三行" * 100).find("\n") == -1,
          "条目清洗去换行")
    check(len(memory_store.clean_text("字" * 500)) == 200, "条目清洗截断 200 字")
    check(memory_store.parse_title_correction("不要叫我Sir，叫我老板") == ("Sir", "老板"),
          "纠正句式解析")
    check(memory_store.parse_forget("删掉那条周报记忆") == "周报", "忘掉句式解析")
    check(memory_store.is_list_request("你记住了什么"), "列记忆句式解析")

    suggestion = memory_store.SuggestionState()
    check(suggestion.maybe("把周报存到桌面", ["把周报存到桌面", "把周报存到桌面"]) ==
          "这个您说过几次了，要我记住当习惯吗？", "同一指令出现 3 次才提问")
    check(suggestion.maybe("把周报存到桌面", ["把周报存到桌面"] * 5) == "",
          "同一会话最多问一次")
    check(suggestion.answer("不用") == "no", "否定后不再问")

if fails:
    print(f"\n失败 {len(fails)} 项")
    sys.exit(1)
print("\nPASS: 记忆模块写入/上限/回滚/多命中不猜删/注入拼装/称呼/建议全过")
