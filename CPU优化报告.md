# 贾维斯待机 CPU 优化报告

日期：2026-11-16
目标：待机总 CPU < 5%，保留语音、自动接管、硬件遥测、全屏/灵动岛、安全确认、记忆能力。

## 一、结论

按 24 逻辑核归一化（与 2026-11-16 的原始测量口径一致）：

| 场景 | 优化前 | 优化后 |
|---|---:|---:|
| 全屏主界面可见 | 9.22% | 1.99% |
| 灵动岛可见 | 4.08% | 0.46% |
| 灵动岛随全屏隐藏 | 仍约 4% | 0.00% |
| 主 Python 进程 | 0.22% | ≤ 0.22%（调度频率只降不升） |
| 全屏模式合计（全屏可见 + 灵动岛隐藏 + 主进程） | 约 14.14% | 约 2.2% |

全屏可见窗口为真实 2560×1540、30 FPS 待命实测：进程树原始合计中位数 47.66，除以 24 核 = 1.99%。全屏隐藏后原始合计中位数 2.34，除以 24 = 0.10%。

## 二、定位到的瓶颈（优化前）

### 1. UI 渲染是绝对主因，不是语音

- `web_fullscreen_ui/hud.js`
  - 700–739：无条件持续 `requestAnimationFrame`，没有待命降帧。
  - 329–343：每帧实时绘制 128 根刻度线，主刻度逐根 `shadowBlur=10`。
  - 364–383：每帧实时绘制 96 个粒子，每个粒子 `shadowBlur=7`。
  - 396：内核光球每帧 `shadowBlur=28+lv*24`。
  - 415：两层声纹每帧 `shadowBlur=12/17`。
  - 467–501：CPU/内存/磁盘共 126 段刻度，每段都带 `shadowBlur`。
- `web_ui/hud.js`
  - 119、140、185、192：能量环、流体环、内核、粒子逐帧高斯模糊。
  - 152–220：连续绘制，220/287 启动后一直 `requestAnimationFrame`，待命仍是 60 FPS。
- `web_capsule.py`
  - 279–289：只调用 Win32 `hide()/show()`，没有显式通知前端停画。全屏打开后灵动岛 WebView 仍会继续合成，这是“两个 WebView 同时耗 CPU”的核心原因。

优化前实测：全屏 GPU 6.05%、renderer 3.17%；灵动岛 GPU 2.58%、renderer 1.50%；主 Python 0.22%。

### 2. 队列与硬件轮询偏勤，但不是主因

- `web_fullscreen.py`
  - 493–542：状态推送固定 25 Hz；`_sample_metrics()` 每 1.0 秒调用 `psutil.cpu_percent()/virtual_memory()/disk_usage()`。
- `jarvis_gui.py`
  - 1091–1107：Tk 兜底渲染每 30 ms（约 33 Hz）。
  - 1109–1142：UI 轮询每 70 ms。
- 这些是低占用，但会持续唤醒 CPU，属于应一起消掉的底噪。

### 3. 语音监听：Whisper 没有“安静时持续推理”

- `whisper_engine.py` 170–219：`_Segmenter` 已用 `audioop.rms` 维护噪声基底与起止点，安静音频只做 RMS。
- `whisper_engine.py` 222–279：`stream.read()` 是阻塞读取，不是 `while True` 空转；整句结束后才 `transcribe()`。
- 但 `whisper_engine.py` 79–95 在启动时加载模型，原来 24 核会开 12 个线程，启动峰值偏大。
- `jarvis.py` 802 起的 Vosk 路径原本每 0.25 秒音频块都送 Kaldi，安静时也会持续推理；本次补了 RMS 前置门控。
- `jarvis.py` 894 起的 WinRT 路径由系统语音引擎做 VAD，Python 侧 `pending.get(timeout=0.2)` 是 5 Hz 等待，没有空转。

### 4. 启动并发

- `jarvis_gui.py` 60–70：4 秒启动画面在主线程串行播完才继续，这属于既定启动动画，不是待机占用。
- `jarvis_gui.py` 870：启动后立刻起线程调用 `jarvis.wake_loop()`，会与 WebView 首帧、模型加载重叠。
- `app_index.py` 只在用户查询软件或 WinRT 热词预热时扫描；Whisper 默认路径不会启动即扫全盘，不是本次待机主因。

## 三、已实施的补丁

### 前端

- `web_fullscreen_ui/hud.js`
  - 288–394：新增离屏缓存。静态径向光晕、透视虚线环、十字准星缓存到 `FX.bg`；128 根刻度环缓存到 `FX.ticks`，每帧只旋转贴图；粒子改成预渲染光点贴图。颜色变化时缓存最多 110–150 ms 重建一次，不再每帧重画。
  - 395–498：反应堆主循环去掉逐刻度、逐粒子的实时高斯模糊；内核和声纹改用“宽半透明描边 + 细实线”模拟辉光。
  - 500–607：声纹、遥测、频谱去掉逐元素 `shadowBlur`；遥测降为约 5.5 Hz 更新。
  - 788–844：新增可见性调度。窗口隐藏立即 `cancelAnimationFrame`；待命 30 FPS；思考/执行/视觉/安全确认或正在说话时 60 FPS；`prefers-reduced-motion` 为 10 FPS。
  - 隐藏时给 `body` 加 `anim-suspended`，CSS 扫描线、脉冲、光标闪烁一起暂停。
- `web_ui/hud.js`
  - 118–143：能量环/流体环改为双描边辉光，去掉 `shadowBlur`。
  - 159–250：新增 20 FPS 待命、45 FPS 活动调度与隐藏停画；声纹活跃时自动升帧。
- `web_fullscreen_ui/style.css`、`web_ui/style.css`
  - 新增 `body.anim-suspended` 规则，暂停全部 CSS 动画，避免 WebView2 在隐藏窗口里继续合成扫描线。

### 后端

- `web_capsule.py`
  - 314–346：新增 `_sync_page_animation()`，显示/隐藏时显式调用 `window.JarvisUI.setAnimating(true/false)`；修复了一个隐藏时事件未清除导致推送线程 100% 空转的问题。
  - 602：窗口隐藏时状态推送从 25 Hz 降到 2 Hz。
- `web_fullscreen.py`
  - 139–181：新增 `_sync_page_animation()`，全屏收起时显式停止前端动画。
  - 520：硬件遥测从 1.0 秒改为 1.8 秒。
  - 561：全屏隐藏时状态推送从约 16.7 Hz 降到 2 Hz。
- `jarvis_gui.py`
  - 1084–1091：启动后延迟 1.5 秒再加载语音模型/启动监听，并把该线程设为 `THREAD_PRIORITY_BELOW_NORMAL`，避免与首帧渲染叠加。
  - 1115：Web/全屏模式 Tk 兜底渲染从 33 Hz 降到 20 Hz。
  - 1151：UI 轮询从约 14 Hz 降到 10 Hz。
- `whisper_engine.py`
  - 87–96：默认线程从 `min(12, cpu//2)` 改为 `min(8, max(4, cpu//3))`；24 核由 12 线程降为 8 线程。可用 `JARVIS_WHISPER_THREADS` 覆盖。
- `jarvis.py`
  - 821–881：Vosk 增加 RMS 前置门控。安静块不送 `AcceptWaveform`；说话结束后只补 3 个静音块让端点收敛，再 `Reset()`。可用 `JARVIS_VOSK_RMS_GATE` 调整阈值（默认 320）。

## 四、验证结果

- Python：`py_compile` 通过（`jarvis.py`、`web_fullscreen.py`、`web_capsule.py`、`jarvis_gui.py`、`whisper_engine.py`）。
- Node：`node --check` 通过（两个 `hud.js`）。
- 界面交互回归：`UI_PROBE_OK`。视觉感知、自动接管、云希→云扬→机械音、折叠/展开侧翼、截图、锁屏、打开记事本、安全确认拒绝均正常。
- 配置回归：`FLAGS_TEST_OK`。`vision_enabled`、`always_listen` 的内存快照与落盘一致。
- 视觉回归：`web_fullscreen_final_preview.png` 已重新离屏渲染；中央魔盒、±24° 双翼、反应堆、四联矩阵、遥测和日志布局保持。
- 全屏待命：真实 2560×1540 可见窗口 30 FPS，中位数 1.99%（24 核归一化）。
- 灵动岛待命：可见 0.46%，随全屏隐藏后 0.00%。
- 全屏隐藏模拟：0.10%。

## 五、启动与复测

1. 完全退出旧实例：托盘右键“退出贾维斯”，确认任务管理器里没有 `pythonw.exe` / `jarvis_gui.py` 子进程。
2. 双击项目根目录的 `start-gui.bat`。
3. 托盘“展开全屏大中枢”进入全屏；等待约 5 秒让 Whisper 加载完成。
4. 任务管理器查看 `pythonw.exe` 与两组 `msedgewebview2.exe`。全屏打开时灵动岛应已隐藏，CPU 应明显低于 5%。

全屏单独预览：

```powershell
cd C:\Users\Ljj13\Desktop\jarvis-desktop-v1.2\jarvis-desktop-v1
.\.venv\Scripts\python.exe web_fullscreen.py --preview
```

或双击 `preview-hud.bat`。

## 六、可调参数

- `JARVIS_WHISPER_THREADS=6`：进一步压低语音模型线程数；越大识别越快。
- `JARVIS_VOSK_RMS_GATE=420`：环境嘈杂时可调高，减少误触发；听不到轻声时调低。
- `JARVIS_NO_CAPTURE_EXCLUDE=1`：仅调试截图时临时关闭窗口采集排除。

## 七、备份

优化前源码已备份到：

`C:\Users\Ljj13\Desktop\jarvis-desktop-v1.2\jarvis-desktop-v1\_backup\cpu-optimization-before-2026-11-16\`

包含 `jarvis.py`、`web_fullscreen.py`、`web_capsule.py`、`jarvis_gui.py`、`whisper_engine.py`、`web_fullscreen_ui/hud.js`、`web_ui/hud.js`。
