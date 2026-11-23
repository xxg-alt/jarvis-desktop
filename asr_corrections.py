# -*- coding: utf-8 -*-
"""ASR 专名纠错表: (错词, 正词, 触发上下文关键词), 门严, 无上下文不纠。"""
import re

_WEATHER = ("天气", "下雨", "温度", "气温", "预报", "城市")
_APP = ("打开", "启动", "关闭", "运行", "启动了没", "装着", "安装")
_TECH = ("本地", "大模型", "注意力", "发展史", "训练", "推理")

CORRECTIONS = [
    ("景德真", "景德镇", _WEATHER),
    ("敬德政", "景德镇", _WEATHER),
    ("仅得着", "景德镇", _WEATHER),
    ("和菲", "合肥", _WEATHER),
    ("兜包", "豆包", _APP),
    ("魔鞋", "模型", _TECH),
    ("TrashForm", "Transformer", _TECH),
    ("trashform", "Transformer", _TECH),
]

PLACE_NAMES = {"景德镇", "合肥", "北京", "上海", "广州", "深圳", "杭州", "南京", "成都",
               "武汉", "西安", "重庆", "苏州", "天津", "长沙", "厦门", "青岛", "大连",
               "郑州", "济南", "沈阳", "哈尔滨", "昆明", "贵阳", "南宁", "福州", "南昌"}


def _place_only(text: str) -> bool:
    value = str(text or "").strip().strip("。！？!?，,")
    return value in PLACE_NAMES or bool(re.fullmatch(r"[\u4e00-\u9fff]{2,6}[市区县省]", value))


def correct_asr(text):
    original = str(text or "")
    result = original
    changes = []
    for wrong, right, contexts in CORRECTIONS:
        if wrong not in result:
            continue
        gated = any(context in original for context in contexts)
        if not gated and wrong in ("景德真", "敬德政", "仅得着", "和菲"):
            gated = _place_only(original)
        if not gated:
            continue
        result = result.replace(wrong, right)
        changes.append((wrong, right))
    return result, changes


def correct_asr_text(text):
    result, changes = correct_asr(text)
    if changes:
        print(f"[ASR纠错] {text} → {result} ({changes})", flush=True)
    return result
