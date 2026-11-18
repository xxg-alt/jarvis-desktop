# -*- coding: utf-8 -*-
"""Windows 自带语音识别 (Windows.Media.SpeechRecognition)

比 66MB 的 Vosk 小模型准得多，而且:
- 完全离线、不花钱、不需要 key
- 支持"热词表": 把唤醒词和桌面软件名塞进去, 识别时优先命中这些词
- 用的就是 Win+H 语音输入那套引擎 (Speech_OneCore), 中文是微软的神经网络模型

对外的两个入口:
  available()                      -> 这台机器能不能用 (引擎/语言包是否就位)
  listen_continuous(on_text, ...)  -> 后台线程持续识别, 每识别出一句就回调
  recognize_once(timeout)          -> 听一句就返回 (给"确认吗?"这类场景用)
"""
import asyncio
import threading
import time

LANG_TAG = "zh-Hans-CN"
_AVAILABLE = None


def _imports():
    from winrt.windows.media.speechrecognition import (  # noqa: F401
        SpeechRecognizer, SpeechRecognitionListConstraint, SpeechRecognitionConfidence,
    )
    from winrt.windows.globalization import Language  # noqa: F401
    return SpeechRecognizer, SpeechRecognitionListConstraint, Language


def available() -> bool:
    """检查系统是否装了中文识别引擎 (只在第一次真正探测)"""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        SpeechRecognizer, _, Language = _imports()
        tags = [l.language_tag for l in SpeechRecognizer.supported_topic_languages]
        _AVAILABLE = LANG_TAG in tags or "zh-CN" in tags
        if not _AVAILABLE:
            print(f"[系统语音] 没有中文识别引擎, 可用: {tags}")
    except Exception as e:
        print(f"[系统语音] 不可用: {type(e).__name__}: {e}")
        _AVAILABLE = False
    return _AVAILABLE


def _language():
    _, _, Language = _imports()
    # 系统里登记的是 zh-Hans-CN; 用 zh-CN 也能匹配到同一个引擎
    return Language(LANG_TAG)


def _build(hotwords=None):
    """建一个识别器, 并把热词塞进去当约束"""
    SpeechRecognizer, SpeechRecognitionListConstraint, _ = _imports()
    rec = SpeechRecognizer(_language())
    words = [w for w in (hotwords or []) if w and len(w) <= 24]
    if words:
        try:
            rec.constraints.append(SpeechRecognitionListConstraint(words[:200], "jarvis"))
        except Exception as e:
            print(f"[系统语音] 热词表加载失败(不影响使用): {e}")
    # 调参: 更愿意等用户说完, 减少半句话就被截断
    try:
        rec.timeouts.initial_silence_timeout = 10_000_000     # 100ns 单位 -> 1.0s
        rec.timeouts.end_silence_timeout = 5_000_000          # 0.5s 静音才结束 (0.09s 太短)
        rec.timeouts.babble_timeout = 60_000_000              # 6s 无效语音
    except Exception:
        pass
    return rec


def recognize_once(timeout: float = 8.0, hotwords=None) -> str:
    """听一句, 返回识别文本; 失败或超时返回空串 (阻塞调用, 不要放主线程)"""
    if not available():
        return ""
    try:
        import asyncio
        from winrt.windows.media.speechrecognition import SpeechRecognitionConfidence

        async def _run():
            rec = _build(hotwords)
            op = rec.recognize_async()
            try:
                result = await asyncio.wait_for(op, timeout)
            except asyncio.TimeoutError:
                try:
                    rec.stop_recognition_async()
                except Exception:
                    pass
                return ""
            if result is None:
                return ""
            if getattr(result, "confidence", None) == SpeechRecognitionConfidence.REJECTED:
                return ""
            return (result.text or "").strip()

        return asyncio.run(_run())
    except Exception as e:
        print(f"[系统语音] 识别失败: {type(e).__name__}: {e}")
        return ""


def listen_continuous(on_text, stop_event=None, hotwords=None, ignore_while=None, on_ready=None):
    """后台线程持续识别; 每识别出一句调用 on_text(text)

    ignore_while: 返回 True 时丢弃这句 (用来跳过贾维斯自己说话时的回声)
    on_ready:     引擎真正开始监听后回调一次 (失败则不回调)
    这个是阻塞函数, 应该在独立线程里跑, 收到 stop_event 后返回。
    """
    if not available():
        raise RuntimeError("系统语音识别不可用")

    async def _run():
        from winrt.windows.media.speechrecognition import SpeechRecognitionConfidence

        rec = _build(hotwords)
        session = rec.continuous_recognition_session
        handlers = []

        def _on_result(sender, args):
            try:
                result = args.result
                if getattr(result, "confidence", None) == SpeechRecognitionConfidence.REJECTED:
                    return
                text = (result.text or "").strip()
                if not text:
                    return
                if ignore_while is not None and ignore_while():
                    return
                on_text(text)
            except Exception as e:
                print(f"[系统语音] 回调异常: {e}")

        def _on_completed(sender, args):
            pass

        h1 = session.add_result_generated(_on_result)
        h2 = session.add_completed(_on_completed)
        handlers.extend([h1, h2])
        try:
            await session.start_async()
            print("[系统语音] 持续识别已启动")
            if on_ready is not None:
                on_ready()
            while stop_event is None or not stop_event.is_set():
                await asyncio.sleep(0.2)
        finally:
            try:
                await session.stop_async()
            except Exception:
                pass
            try:
                session.remove_result_generated(h1)
                session.remove_completed(h2)
            except Exception:
                pass

    asyncio.run(_run())
