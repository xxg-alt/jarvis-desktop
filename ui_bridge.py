# -*- coding: utf-8 -*-
"""
贾维斯 UI 状态事件总线 (ui_bridge)

业务层 (jarvis.py: 语音/截图/自动化/LLM) 只管往这里丢事件，
界面层 (capsule.py / hud_overlay.py) 只管在这里取快照 —— 两边不互相 import。

设计约束:
  1. emit_* 可以从任意线程调用 (语音线程 / 业务线程 / GUI 线程), 不阻塞、不抛异常;
  2. 视图层只在 Tk 主线程里调用 snapshot(), 订阅回调里禁止碰 Tk;
  3. 状态用"最新值"语义 (不排队), 所以丢帧也不会卡在旧状态上。

用法 (业务侧):
    import ui_bridge
    ui_bridge.emit_state("THINKING")

用法 (视图侧):
    snap = ui_bridge.snapshot()        # 每帧拉一次
    ok = ui_bridge.request_guard_confirm(...)   # 业务线程阻塞等, 视图调 resolve_guard()
"""
import threading
import time

# ── 六个标准状态 ──
IDLE        = "IDLE"         # 待命
LISTENING   = "LISTENING"    # 正在收音
PERCEIVING  = "PERCEIVING"   # 屏幕感知 / 截图
THINKING    = "THINKING"     # 大模型思考
ACTING      = "ACTING"       # 执行动作
GUARD       = "GUARD"        # 高危动作待确认

STATES = (IDLE, LISTENING, PERCEIVING, THINKING, ACTING, GUARD)

STATE_LABELS = {
    IDLE:       "待命",
    LISTENING:  "聆听中",
    PERCEIVING: "视觉扫描",
    THINKING:   "思考中",
    ACTING:     "执行中",
    GUARD:      "等待确认",
}

GUARD_TIMEOUT = 15.0         # 确认超时 (秒), 超时按"取消"处理

# ── 内部状态 ──
_lock = threading.Lock()
_state = IDLE
_level = 0.0
_subtitle = ("", True, 0.0)          # (文本, 是否最终, 时间戳)
_targets = []                        # [{"action","x","y","w","h","ts","ttl"}]
_guard = None                        # {"desc": str, "event": Event, "result": bool, "ts": float}
_flags = {                           # 用户可切换的长期能力开关 (全屏 / 灵动岛共用)
    "vision_enabled": True,
    "always_listen": True,
}
_seq = 0                             # 任何变化都会 +1, 视图据此判断要不要重绘
_subscribers = []                    # 视图注册的回调 (仅用于"有界面在跑"的判断 + 脏标记)


def _bump():
    global _seq
    _seq += 1


# ═══════════════════════════════════════════════════════
#  业务侧 → 总线
# ═══════════════════════════════════════════════════════
def emit_state(state_name):
    """切换状态: IDLE / LISTENING / PERCEIVING / THINKING / ACTING / GUARD"""
    global _state
    name = str(state_name or "").upper()
    if name not in STATES:
        return
    with _lock:
        if _state != name:
            _state = name
            _bump()
    _notify()


def emit_audio_level(float_val):
    """推送麦克风音量 0.0 ~ 1.0"""
    global _level
    try:
        v = float(float_val)
    except (TypeError, ValueError):
        return
    v = 0.0 if v < 0 else (1.0 if v > 1 else v)
    with _lock:
        _level = v
        _bump()


def emit_subtitle(text, is_final=False):
    """推送字幕: is_final=False 是实时识别的中间结果, True 是 AI 最终回复。

    允许 text 为空: 表示“当前这句已经播完，清空字幕”，供全屏页淡出后隐藏。
    """
    global _subtitle
    text = str(text or "").strip()
    with _lock:
        _subtitle = (text, bool(is_final), time.time())
        _bump()
    _notify()


def emit_hud_target(action_type, x, y, w, h, ttl=4.0):
    """让穿透层在屏幕坐标 (物理像素) 上画角标框 / 准星"""
    try:
        item = {
            "action": str(action_type or "target"),
            "x": int(x), "y": int(y), "w": int(w), "h": int(h),
            "ts": time.time(), "ttl": float(ttl),
        }
    except (TypeError, ValueError):
        return
    with _lock:
        _targets.append(item)
        del _targets[:-4]           # 最多同时画 4 个, 防止堆积
        _bump()
    _notify()


def clear_hud_targets():
    with _lock:
        if _targets:
            _targets.clear()
            _bump()
    _notify()


def update_flags(**kwargs):
    """更新视图可见的能力开关。只接受已知字段，值强制为 bool。

    典型用途:
        vision_enabled  —— 截图时是否进入视觉感知状态并绘制扫描框
        always_listen   —— 是否免唤醒自动接管语音指令
    """
    global _flags
    allowed = {"vision_enabled", "always_listen"}
    changed = False
    with _lock:
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            next_value = bool(value)
            if _flags.get(key) != next_value:
                _flags[key] = next_value
                changed = True
        if changed:
            _bump()
    if changed:
        _notify()


def snapshot_flags():
    with _lock:
        return dict(_flags)


def request_guard_confirm(action_desc, timeout=GUARD_TIMEOUT):
    """高危动作挂起当前线程, 等用户在胶囊上点确认/取消。

    返回:
        True  —— 用户确认
        False —— 用户取消 或 超时
        None  —— 当前没有界面在跑 (没有任何视图订阅), 调用方应退回旧的语音/终端确认
    """
    global _guard
    with _lock:
        if not _subscribers:
            return None
        _guard = {
            "desc": str(action_desc or ""),
            "event": threading.Event(),
            "result": False,
            "ts": time.time(),
        }
        guard = _guard
        _bump()
    emit_state(GUARD)
    _notify()

    answered = guard["event"].wait(max(1.0, float(timeout)))
    with _lock:
        result = bool(guard["result"]) if answered else False
        if _guard is guard:
            _guard = None
            _bump()
    emit_state(IDLE)
    return result


def resolve_guard(ok):
    """视图侧调用: 用户点了确认(True)/取消(False)"""
    with _lock:
        guard = _guard
        if guard is None:
            return False
        guard["result"] = bool(ok)
        guard["event"].set()
    return True


# ═══════════════════════════════════════════════════════
#  视图侧 → 总线
# ═══════════════════════════════════════════════════════
def snapshot():
    """取当前状态快照 (整包复制, 视图随便改)"""
    now = time.time()
    with _lock:
        targets = [dict(t) for t in _targets if now - t["ts"] < t["ttl"]]
        guard = None
        if _guard is not None:
            guard = {"desc": _guard["desc"], "ts": _guard["ts"]}
        return {
            "state": _state,
            "level": _level,
            "subtitle": _subtitle[0],
            "subtitle_final": _subtitle[1],
            "subtitle_ts": _subtitle[2],
            "targets": targets,
            "guard": guard,
            "flags": dict(_flags),
            "seq": _seq,
            "ts": now,
        }


def subscribe(callback):
    """注册视图回调。仅用于: 让总线知道"有界面", 以及唤醒视图刷新。

    注意: callback 可能在业务线程被调用, 里面禁止操作 Tk 控件。
    """
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback):
    if callback in _subscribers:
        _subscribers.remove(callback)


def has_view():
    return bool(_subscribers)


def current_state():
    return _state


def _notify():
    for cb in list(_subscribers):
        try:
            cb()
        except Exception:
            pass


# ═══════════════ 独立测试 ═══════════════
if __name__ == "__main__":
    def _fake_view():
        pass

    subscribe(_fake_view)
    print("有界面:", has_view())
    print("确认结果:", request_guard_confirm("测试: 关机"))  # 15 秒超时 → False
    for s in STATES:
        emit_state(s)
        emit_audio_level(0.42)
        print(snapshot()["state"], snapshot()["level"])
