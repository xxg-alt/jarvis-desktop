# -*- coding: utf-8 -*-
"""可自适应主人偏好的长期记忆: 独立 JSON + append-only 变更日志 + 回滚。"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

DEFAULT_TITLE = "先生"
MAX_CHARS = 200
MAX_ENTRIES = 100
PROMPT_LIMIT = 40
KINDS = ("preference", "habit", "fact")
SEED_PREFERENCE = {"id": "seed-title", "text": "称呼=" + DEFAULT_TITLE,
                   "kind": "preference", "source": "explicit", "created": 0, "active": True}

_SECRET_RE = re.compile(
    r"(?i)(api[\s_-]?key|access[\s_-]?token|token|password|passwd|密码|密钥|令牌)"
    r"(\s*[:：=]\s*)([^\s,，;；]{3,})"
)

REMEMBER_RE = re.compile(
    r"^(?:请|帮我|给我)?(?:记住|记一下|记住一下|别忘了)(?:，|,|：|:)?\s*(.+)$"
)
FORGET_RE = re.compile(
    r"^(?:请|帮我|给我)?(?:忘掉|忘记|删掉|删除)(?:那条|这条|刚才那条|这条记忆)?"
    r"(?:记忆)?(?:，|,|：|:)?\s*(.+)$"
)
LIST_PHRASES = {
    "你记住了什么", "你记了什么", "记住了什么", "记了什么", "你都记住了什么",
    "你记住了啥", "记住了啥", "记忆里有什么", "你记得什么", "长期记忆有哪些",
}
TITLE_CHANGE_RE = re.compile(
    r"^(?:不要|别)叫我(.{1,20}?)[，,。]?\s*(?:就)?(?:叫我|改叫|称我为|改成)\s*(.{1,20}?)[。！!？?]*$"
)
TITLE_DROP_RE = re.compile(r"^(?:不要|别)叫我(.{1,20}?)[。！!？?]*$")
TITLE_SET_RE = re.compile(r"^(?:以后|接下来|请|就)?叫我(.{1,20}?)[。！!？?]*$")
SELECT_RE = re.compile(r"^第\s*(\d+)\s*条$")


def _default_path() -> Path:
    override = os.environ.get("JARVIS_MEMORY_STORE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    memory_file = os.environ.get("JARVIS_MEMORY_FILE", "").strip()
    base = (Path(memory_file).expanduser() if memory_file
            else Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis" / "memory.json")
    return base.parent / "memory_store.json"


def clean_text(value, limit=MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    text = _SECRET_RE.sub(r"\1\2[已隐去]", text)
    return text[:limit]


def normalize(value) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(value or "").lower())[:300]


def guess_kind(text: str) -> str:
    value = str(text or "")
    if any(w in value for w in ("称呼", "喜欢", "讨厌", "偏好", "不爱", "喜欢的")):
        return "preference"
    if any(w in value for w in ("以后", "每次", "每天", "每逢", "习惯")):
        return "habit"
    return "fact"


def parse_remember(text: str) -> str:
    match = REMEMBER_RE.match(str(text or "").strip())
    if not match:
        return ""
    value = clean_text(match.group(1))
    return value if len(value) >= 2 else ""


def parse_forget(text: str) -> str:
    match = FORGET_RE.match(str(text or "").strip())
    if not match:
        return ""
    value = clean_text(match.group(1))
    value = re.sub(r"(?:的|这条|那条|记忆)+$", "", value).strip()
    return value if value else ""


def is_list_request(text: str) -> bool:
    return normalize(text) in {normalize(x) for x in LIST_PHRASES}


def parse_title_correction(text: str):
    value = str(text or "").strip()
    match = TITLE_CHANGE_RE.match(value)
    if match:
        return clean_text(match.group(1), 20), clean_text(match.group(2), 20)
    match = TITLE_DROP_RE.match(value)
    if match:
        return clean_text(match.group(1), 20), DEFAULT_TITLE
    match = TITLE_SET_RE.match(value)
    if match:
        return "", clean_text(match.group(1), 20)
    return None


class MemoryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else _default_path()
        self.changelog_path = self.path.with_name(self.path.stem + "_changes.jsonl")
        self._lock = threading.RLock()
        self.entries = self._load()

    def _load(self):
        try:
            if not self.path.exists():
                return []
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("entries", []) if isinstance(data, dict) else data
            if not isinstance(raw, list):
                return []
            out = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                text = clean_text(item.get("text"))
                if not text or item.get("active") is False:
                    continue
                out.append({
                    "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                    "text": text,
                    "kind": item.get("kind") if item.get("kind") in KINDS else "fact",
                    "source": item.get("source") if item.get("source") in
                    ("explicit", "correction", "suggested") else "explicit",
                    "created": float(item.get("created") or time.time()),
                    "active": True,
                })
            return out[-MAX_ENTRIES:]
        except Exception as exc:
            print(f"[自适应记忆] 读取失败, 使用空记忆: {exc}")
            return []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(
            json.dumps({"version": 1, "entries": self.entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _log_change(self, op: str, before: list, target_id: str = ""):
        record = {
            "ts": time.time(),
            "op": op,
            "target_id": target_id,
            "before": [dict(item) for item in before],
            "after": [dict(item) for item in self.entries],
        }
        self.changelog_path.parent.mkdir(parents=True, exist_ok=True)
        with self.changelog_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def add(self, text, kind="fact", source="explicit"):
        raw = str(text or "")
        value = clean_text(raw)
        if len(raw.strip()) > MAX_CHARS or not value:
            return {"ok": False, "say": f"这条太长，单条记忆不能超过{MAX_CHARS}字，我没记下。"}
        if len(value) < 2:
            return {"ok": False, "say": "这条记忆太短了，我没记下。"}
        with self._lock:
            before = [dict(item) for item in self.entries]
            if len(self.entries) >= MAX_ENTRIES:
                return {"ok": False, "say": f"长期记忆已经满了（{MAX_ENTRIES}条），先忘掉一条再记吧。"}
            entry = {
                "id": uuid.uuid4().hex[:12],
                "text": value,
                "kind": kind if kind in KINDS else "fact",
                "source": source if source in ("explicit", "correction", "suggested") else "explicit",
                "created": time.time(),
                "active": True,
            }
            self.entries.append(entry)
            self._save()
            self._log_change("add", before, entry["id"])
        return {"ok": True, "say": f"记住了：{value}", "entry": dict(entry)}

    def set_title(self, title):
        value = clean_text(title, 20)
        if not value:
            return {"ok": False, "say": "称呼没听清，您再说一遍。"}
        with self._lock:
            before = [dict(item) for item in self.entries]
            target = next((item for item in self.entries
                           if item.get("active") and item.get("kind") == "preference"
                           and str(item.get("text", "")).startswith("称呼=")), None)
            if target:
                target["text"] = "称呼=" + value
                target["source"] = "correction"
                target["created"] = time.time()
                target_id = target["id"]
            elif len(self.entries) >= MAX_ENTRIES:
                return {"ok": False, "say": f"长期记忆已经满了（{MAX_ENTRIES}条），先忘掉一条再记吧。"}
            else:
                target = {
                    "id": uuid.uuid4().hex[:12],
                    "text": "称呼=" + value,
                    "kind": "preference",
                    "source": "correction",
                    "created": time.time(),
                    "active": True,
                }
                self.entries.append(target)
                target_id = target["id"]
            self._save()
            self._log_change("set_title", before, target_id)
        return {"ok": True, "say": f"好的，以后我称呼您为{value}。", "entry": dict(target)}

    def current_title(self) -> str:
        with self._lock:
            for item in reversed(self.entries):
                if (item.get("active") and item.get("kind") == "preference"
                        and str(item.get("text", "")).startswith("称呼=")):
                    value = clean_text(item["text"].split("=", 1)[1], 20)
                    return value or DEFAULT_TITLE
        return DEFAULT_TITLE

    def forget(self, keyword, choose_id=""):
        value = clean_text(keyword)
        with self._lock:
            target = None
            if choose_id:
                target = next((item for item in self.entries if item["id"] == choose_id), None)
            elif value and any(item["id"] == value for item in self.entries):
                target = next(item for item in self.entries if item["id"] == value)
            if target:
                before = [dict(item) for item in self.entries]
                self.entries = [item for item in self.entries if item["id"] != target["id"]]
                self._save()
                self._log_change("forget", before, target["id"])
                return {"ok": True, "say": f"已忘掉：{target['text']}", "entry": dict(target)}
            if not value:
                return {"ok": False, "say": "要忘掉哪一条？说个关键词就行。"}
            needle = normalize(value)
            matches = [item for item in self.entries if needle and needle in normalize(item.get("text", ""))]
            if not matches:
                return {"ok": False, "say": f"没找到和“{value}”有关的长期记忆。"}
            if len(matches) > 1:
                summary = "；".join(f"{i + 1}. {item['text'][:30]}" for i, item in enumerate(matches[:5]))
                return {"ok": False, "ambiguous": True, "entries": [dict(item) for item in matches],
                        "say": f"我找到{len(matches)}条和“{value}”有关的记忆：{summary}。您说第几条，我再删。"}
            item = matches[0]
            before = [dict(item2) for item2 in self.entries]
            self.entries = [item2 for item2 in self.entries if item2["id"] != item["id"]]
            self._save()
            self._log_change("forget", before, item["id"])
            return {"ok": True, "say": f"已忘掉：{item['text']}", "entry": dict(item)}

    def rollback(self):
        with self._lock:
            record = None
            try:
                if self.changelog_path.exists():
                    for line in self.changelog_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if isinstance(item, dict) and item.get("op") != "rollback":
                            record = item
            except Exception as exc:
                print(f"[自适应记忆] 变更日志读取失败: {exc}")
            if not record:
                return {"ok": False, "say": "还没有可以回滚的记忆变更。"}
            before = [dict(item2) for item2 in self.entries]
            self.entries = [dict(item2) for item2 in record.get("before", [])]
            self._save()
            self._log_change("rollback", before, str(record.get("target_id") or ""))
        return {"ok": True, "say": "已撤销最近一次记忆写入。"}

    def stats_text(self):
        with self._lock:
            items = list(self.entries)
        recent = items[-5:][::-1]
        head = f"我记住了{len(items)}条长期记忆"
        if not recent:
            return head + "，还没什么内容。"
        summary = "；".join(f"{i + 1}. {item['text'][:30]}" for i, item in enumerate(recent))
        return head + "，最近5条是：" + summary + "。"

    def prompt_block(self):
        with self._lock:
            items = [dict(item) for item in self.entries if item.get("active")]
        items.sort(key=lambda item: (str(item.get("kind", "")), float(item.get("created") or 0),
                                     str(item.get("id", ""))))
        lines = ["【主人长期记忆】", "- " + SEED_PREFERENCE["text"] + "（默认称呼，用户改过则以记忆中的称呼为准）"]
        for item in items[:PROMPT_LIMIT]:
            lines.append(f"- [{item['kind']}] {item['text']}")
        lines.append("称呼用户用记忆中的称呼（默认先生），不要用 Sir/主人。")
        return "\n".join(lines)


class SuggestionState:
    def __init__(self):
        self.asked = False
        self.pending = ""
        self.dismissed = set()

    def answer(self, text):
        if not self.pending:
            return ""
        low = normalize(text)
        if not low:
            return ""
        no = any(w in low for w in ("不用", "不要", "算了", "别记", "不需要", "不记"))
        yes = any(w in low for w in ("好", "行", "可以", "要", "嗯", "记住", "当习惯", "是的"))
        if no:
            self.dismissed.add(normalize(self.pending))
            self.pending = ""
            return "no"
        if yes:
            return "yes"
        return ""

    def maybe(self, command, history):
        if self.asked:
            return ""
        norm = normalize(command)
        if not norm or norm in self.dismissed:
            return ""
        count = sum(1 for text in history if normalize(text) == norm) + 1
        if count < 3:
            return ""
        self.asked = True
        self.pending = str(command or "")
        return "这个您说过几次了，要我记住当习惯吗？"


_STORE = None
_STORE_LOCK = threading.Lock()
SUGGESTION = SuggestionState()


def get_store() -> MemoryStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemoryStore()
        return _STORE


def set_store(store):
    global _STORE
    with _STORE_LOCK:
        _STORE = store


def current_title() -> str:
    return get_store().current_title()
