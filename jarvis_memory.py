# -*- coding: utf-8 -*-
"""
贾维斯长期记忆
- 保存最近对话，让大模型理解“再一点”“刚才那个”等指代
- 保存成功执行过的动作和用户纠正，作为后续相似指令的学习案例
- 保存用户明确要求“记住”的笔记
- 全部数据只写本机 %APPDATA%\\Jarvis\\memory.json，可语音清空
"""
import json
import os
import re
import threading
import time
from pathlib import Path


def _default_path() -> Path:
    override = os.environ.get("JARVIS_MEMORY_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis" / "memory.json"


_SECRET_RE = re.compile(
    r"(?i)(api[\s_-]?key|access[\s_-]?token|token|password|passwd|密码|密钥|令牌)"
    r"(\s*[:：=]\s*)([^\s,，;；]{3,})"
)
_CORRECTION_RE = re.compile(
    r"(^(?:不是|不对)(?:，|,|。|$)|不是这个|不是让你|不是我要|不对(?:，|,|。|$)|你听错|"
    r"理解错|搞错|我说的是|我是要你|我要的是|重新理解|不是那个)"
)
_NOTE_RE = re.compile(
    r"^(?:请|帮我|给我)?(?:记住|记一下|记住一下|别忘了)(?:，|,|：|:)?\s*(.+)$"
)
_MAX_CONVERSATION = 240
_MAX_NOTES = 120
_MAX_LEARNED = 240
_MAX_CORRECTIONS = 120


def _clean_text(value, limit=1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SECRET_RE.sub(r"\1\2[已隐去]", text)
    return text[:limit]


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "").lower())[:300]


def _bigrams(text: str) -> set:
    normalized = _normalize(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i:i + 2] for i in range(len(normalized) - 1)}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _clean_plan(plan) -> dict:
    if not isinstance(plan, dict):
        return {}
    action = _clean_text(plan.get("action"), 60)
    if not action:
        return {}
    args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
    try:
        args_text = _clean_text(json.dumps(args, ensure_ascii=False, sort_keys=True), 500)
    except Exception:
        args_text = ""
    return {
        "action": action,
        "args": args_text,
        "say": _clean_text(plan.get("say"), 300),
    }


class JarvisMemory:
    def __init__(self, path=None):
        self.path = Path(path) if path else _default_path()
        self._lock = threading.RLock()
        self.data = self._load()

    def _empty(self) -> dict:
        return {
            "version": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
            "conversations": [],
            "notes": [],
            "learned": [],
            "corrections": [],
        }

    def _load(self) -> dict:
        data = self._empty()
        try:
            if not self.path.exists():
                return data
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return data
            for key in ("conversations", "notes", "learned", "corrections"):
                if isinstance(loaded.get(key), list):
                    data[key] = loaded[key]
            data["created_at"] = loaded.get("created_at", data["created_at"])
            data["updated_at"] = loaded.get("updated_at", data["updated_at"])
        except Exception as exc:
            print(f"[记忆] 读取失败，使用空记忆: {exc}")
        return data

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data["updated_at"] = time.time()
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except Exception as exc:
            print(f"[记忆] 保存失败: {exc}")

    def _trim(self):
        self.data["conversations"] = self.data["conversations"][-_MAX_CONVERSATION:]
        self.data["notes"] = self.data["notes"][-_MAX_NOTES:]
        self.data["learned"] = self.data["learned"][-_MAX_LEARNED:]
        self.data["corrections"] = self.data["corrections"][-_MAX_CORRECTIONS:]

    def record_turn(self, user_text: str, plan=None, source: str = "local"):
        """记录一轮用户消息与助手动作；成功的电脑操作会进入学习案例。"""
        user = _clean_text(user_text, 800)
        if not user:
            return
        clean_plan = _clean_plan(plan)
        now = time.time()
        with self._lock:
            self.data["conversations"].append({
                "role": "user", "text": user, "ts": now,
            })
            assistant_text = clean_plan.get("say", "") or (
                "未识别出可执行动作" if not clean_plan else "已处理"
            )
            self.data["conversations"].append({
                "role": "assistant",
                "text": _clean_text(assistant_text, 500),
                "action": clean_plan.get("action", ""),
                "args": clean_plan.get("args", ""),
                "source": _clean_text(source, 30),
                "ts": now,
            })
            action = clean_plan.get("action", "")
            if action and action != "chat":
                learned = {
                    "command": user,
                    "action": action,
                    "args": clean_plan.get("args", ""),
                    "say": clean_plan.get("say", ""),
                    "ts": now,
                }
                # 同一说法只保留最新一次，避免重复案例占满上下文。
                self.data["learned"] = [
                    item for item in self.data["learned"]
                    if _normalize(item.get("command", "")) != _normalize(user)
                ]
                self.data["learned"].append(learned)
            self._trim()
            self._save()

    def last_exchange(self):
        """返回上一条用户指令及紧随其后的助手记录。"""
        with self._lock:
            conversations = self.data["conversations"]
            for index in range(len(conversations) - 1, -1, -1):
                if conversations[index].get("role") != "user":
                    continue
                user = conversations[index].get("text", "")
                assistant = {}
                for item in conversations[index + 1:]:
                    if item.get("role") == "assistant":
                        assistant = item
                        break
                return {"user": user, "assistant": assistant}
        return {}

    @staticmethod
    def is_correction(text: str) -> bool:
        return bool(_CORRECTION_RE.search(str(text or "")))

    def record_correction(self, previous_command: str, corrected_command: str, plan):
        clean_plan = _clean_plan(plan)
        if not clean_plan or clean_plan.get("action") == "chat":
            return
        before = _clean_text(previous_command, 500)
        after = _clean_text(corrected_command, 500)
        if not before or not after:
            return
        with self._lock:
            self.data["corrections"].append({
                "before": before,
                "after": after,
                "action": clean_plan.get("action", ""),
                "args": clean_plan.get("args", ""),
                "say": clean_plan.get("say", ""),
                "ts": time.time(),
            })
            self._trim()
            self._save()

    def extract_note(self, text: str) -> str:
        match = _NOTE_RE.match(str(text or "").strip())
        if not match:
            return ""
        note = _clean_text(match.group(1), 800)
        return note if len(note) >= 2 else ""

    def add_note(self, note: str):
        note = _clean_text(note, 800)
        if not note:
            return
        with self._lock:
            self.data["notes"] = [
                item for item in self.data["notes"]
                if _normalize(item.get("text", "")) != _normalize(note)
            ]
            self.data["notes"].append({"text": note, "ts": time.time()})
            self._trim()
            self._save()

    @staticmethod
    def control_command(text: str) -> str:
        normalized = _normalize(text)
        if normalized in {
            "清空记忆", "清空对话记忆", "清除记忆", "忘记所有记忆",
            "忘掉所有对话", "删除所有记忆", "重置记忆",
        }:
            return "clear"
        if normalized in {"查看记忆", "记忆状态", "你记得什么", "你还记得什么"}:
            return "status"
        return ""

    def clear(self):
        with self._lock:
            self.data = self._empty()
            self._save()

    def stats(self) -> dict:
        with self._lock:
            user_count = sum(
                1 for item in self.data["conversations"]
                if item.get("role") == "user"
            )
            return {
                "turns": user_count,
                "notes": len(self.data["notes"]),
                "learned": len(self.data["learned"]),
                "corrections": len(self.data["corrections"]),
                "path": str(self.path),
            }

    def stats_text(self) -> str:
        stats = self.stats()
        return (
            f"我保存了{stats['turns']}轮对话、{stats['notes']}条明确笔记、"
            f"{stats['learned']}个操作案例和{stats['corrections']}个纠正案例。"
        )

    @staticmethod
    def _select_relevant(items, current_text, text_getter, limit=3):
        ranked = []
        for index, item in enumerate(items):
            score = _similarity(current_text, text_getter(item))
            ranked.append((score, index, item))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        relevant = [item for score, _, item in ranked[:limit] if score > 0]
        if not relevant:
            relevant = items[-min(2, len(items)):]
        return relevant

    @staticmethod
    def _rag_entries(notes, learned, corrections, older):
        """整理成 rag_engine.sync_memory 需要的 (kind, 检索文本, 原始item) 列表。"""
        entries = []
        for item in notes:
            text = str(item.get("text", "")).strip()
            if text:
                entries.append(("note", text, item))
        for item in learned:
            text = str(item.get("command", "")).strip()
            if text:
                entries.append(("learned", text, item))
        for item in corrections:
            text = str(item.get("after", "")).strip()
            if text:
                entries.append(("correction", text, item))
        for item in older:
            text = str(item.get("text", "")).strip()
            if len(text) >= 4:
                who = "用户说：" if item.get("role") == "user" else "助手答："
                entries.append(("conversation", who + text, item))
        return entries

    def build_context(self, current_text: str) -> str:
        """构建给大模型的最小必要记忆块；历史不会覆盖当前指令。
        RAG 语义检索可用时按意思召回，不可用时自动退回字面相似度。"""
        with self._lock:
            all_convs = list(self.data["conversations"])
            # 最近 10 轮已经由 llm_brain 的消息窗口原样带进上下文, 这里不再重复拼一遍:
            # 重复既多花 token, 又会让每轮前缀都发生变化, 直接拉低缓存命中率
            older = all_convs[:-10] if len(all_convs) > 10 else []
            notes = list(self.data["notes"])
            learned = list(self.data["learned"])
            corrections = list(self.data["corrections"])

        lines = [
            "【记忆上下文】",
            "历史仅用于理解指代、延续话题和学习用户纠正；当前用户输入才是本次唯一任务，"
            "不要重复执行历史动作。",
        ]

        # 语义检索（RAG）：按意思召回，而不是字面撞词
        rag_note = rag_learn = rag_corr = rag_conv = None
        try:
            import rag_engine
            if rag_engine.sync_memory(self._rag_entries(notes, learned, corrections, older)):
                rag_note = rag_engine.search_mem(current_text, kinds=("note",), top_k=3)
                rag_learn = rag_engine.search_mem(current_text, kinds=("learned",), top_k=3)
                rag_corr = rag_engine.search_mem(current_text, kinds=("correction",), top_k=3)
                rag_conv = rag_engine.search_mem(current_text, kinds=("conversation",), top_k=1)
        except Exception as exc:
            print(f"[记忆] 语义检索不可用, 退回字面匹配: {exc}")

        def _pick(rag_hits, items, getter, limit=3):
            if rag_hits:
                return [hit["item"] for hit in rag_hits]
            return self._select_relevant(items, current_text, getter, limit=limit)

        relevant_notes = _pick(rag_note, notes, lambda item: item.get("text", ""))
        if relevant_notes:
            lines.append("【用户明确要求记住的事】")
            for item in relevant_notes:
                lines.append(f"- {_clean_text(item.get('text', ''), 400)}")

        relevant_learned = _pick(rag_learn, learned, lambda item: item.get("command", ""))
        if relevant_learned:
            lines.append("【相似历史操作】")
            for item in relevant_learned:
                lines.append(
                    f"- 用户说“{_clean_text(item.get('command', ''), 220)}”时，"
                    f"应执行 {_clean_text(item.get('action', ''), 60)}，"
                    f"参数 {_clean_text(item.get('args', ''), 250)}"
                )

        relevant_corrections = _pick(rag_corr, corrections, lambda item: item.get("after", ""))
        if relevant_corrections:
            lines.append("【用户纠正案例】")
            for item in relevant_corrections:
                lines.append(
                    f"- 用户原话“{_clean_text(item.get('before', ''), 200)}”被理解错误；"
                    f"用户纠正“{_clean_text(item.get('after', ''), 200)}”后，"
                    f"应执行 {_clean_text(item.get('action', ''), 60)}，"
                    f"参数 {_clean_text(item.get('args', ''), 250)}"
                )

        if rag_conv:
            lines.append("【更早的相关对话】")
            for hit in rag_conv:
                item = hit["item"]
                role = "用户" if item.get("role") == "user" else "助手"
                text = _clean_text(item.get("text", ""), 220)
                action = _clean_text(item.get("action", ""), 60)
                if role == "助手" and action:
                    lines.append(f"{role}：{text}（执行动作 {action}）")
                else:
                    lines.append(f"{role}：{text}")

        return "\n".join(lines)[:2400]
