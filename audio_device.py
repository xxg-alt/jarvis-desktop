# -*- coding: utf-8 -*-
"""进程级 PortAudio 设备管理器

为什么需要这一层
----------------
PortAudio 是进程级的全局资源：`pyaudio.PyAudio()` 触发 Pa_Initialize()，
`pa.terminate()` 触发 Pa_Terminate()。二者是引用计数配对的。

旧代码里 6 个地方各自 `PyAudio()` 再各自 `terminate()`，其中 TTS 播报
（edge_speech）和机械音兜底（robot_voice）是**每说一句话就 terminate 一次**。
whisper 的常驻输入流还挂在同一个 PortAudio 上，于是：

    贾维斯说完一句 → pa.terminate() 拆掉全局 PortAudio
    → whisper 输入流变成悬空流，stream.read() 开始失败
    → 旧循环里 `except Exception: continue` 把故障吞掉，无日志、无自愈
    → 语音永久沉默，界面停在 LISTENING（用户看到的就是"卡死"）

修法（第一性原理）：PortAudio 只初始化一次、只在进程退出时释放。
所有模块共用同一个 PyAudio 实例，各自只关闭**自己的 stream**。

对外接口
--------
    get_pyaudio()        -> 共享的 PyAudio 实例（线程安全，懒加载）
    open_input(...)      -> 打开输入流（失败会抛原始异常，调用方决定如何自愈）
    open_output(...)     -> 打开输出流
    release_stream(s)    -> 只关这一个流，不动 PortAudio
    shutdown()           -> 仅进程退出时调用
"""
import atexit
import threading

_LOCK = threading.RLock()
_PA = None
_SHUTDOWN = False


def get_pyaudio():
    """返回进程内唯一的 PyAudio 实例；首个调用者负责初始化。"""
    global _PA, _SHUTDOWN
    with _LOCK:
        if _SHUTDOWN:
            # 退出流程里再要设备就明确报错，不要还原出一个半死的 PortAudio
            raise RuntimeError("PortAudio 已关闭，无法再打开设备")
        if _PA is None:
            import pyaudio
            _PA = pyaudio.PyAudio()
        return _PA


def open_input(**kwargs):
    """打开输入流。rate/channels/format/frames_per_buffer 由调用方给全。"""
    pa = get_pyaudio()
    return pa.open(**kwargs)


def open_output(**kwargs):
    """打开输出流。"""
    pa = get_pyaudio()
    return pa.open(**kwargs)


def release_stream(stream, stop=True):
    """只关闭传入的这一条流；**绝不** terminate 全局 PortAudio。"""
    if stream is None:
        return
    if stop:
        try:
            stream.stop_stream()
        except Exception:
            pass
    try:
        stream.close()
    except Exception:
        pass


def shutdown():
    """仅在进程退出时调用一次，释放 PortAudio。"""
    global _PA, _SHUTDOWN
    with _LOCK:
        if _SHUTDOWN:
            return
        _SHUTDOWN = True
        if _PA is not None:
            try:
                _PA.terminate()
            except Exception:
                pass
            _PA = None


atexit.register(shutdown)
