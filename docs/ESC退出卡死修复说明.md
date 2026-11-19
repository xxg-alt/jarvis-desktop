# 按 ESC 会退出、会卡死 —— 根因与修复

修复日期：2026-11-19　　分支：main　　回滚目录：`_backup\esc-exit-fix-before-20261119`

---

## 一、先把"退出"这个词拆开

"按 ESC 会退出会卡死"其实是两种完全不同的故障，体感却一样：

| 形态 | 进程 | 屏幕 | 用户感知 |
|---|---|---|---|
| A 真退出 | 消失（pythonw=0） | 无 | 程序没了 |
| B 假退出 | **还活着** | **什么都不剩** | "退出了" |
| C 冻住 | 还活着 | 窗口在，点哪都没反应 | "卡死了" |

代码层面三种都能发生，而且它们**共用同一条路径**：ESC → 收起全屏 → 显示胶囊。

这条路径上原来没有任何机制保证"新的载体一定看得见"。只要中间任一步静默失败，屏幕就空了；主进程还在 `root.mainloop()` 里继续识别声音、继续播报，于是"退出"和"卡死"同时成立。

**这就是第一性原理的入口：ESC 的语义是"换载体"，不是"消灭载体"。任何时刻屏幕上必须有一个看得见、点得动的界面。**

---

## 二、取证（不是猜）

| 手段 | 结果 |
|---|---|
| 枚举全部可见顶层窗口 | 全屏期标题为 `J.A.R.V.I.S. 战术全息指挥中枢`；ESC 后只剩胶囊/其它窗口 |
| 数进程 | 结束主进程后 `msedgewebview2` **残留 6~12 个**，点不动的灰窗口就在这 |
| 读 `jarvis-gui.log` | 发现 `_tkinter.TclError: bad window path name ".!toplevel3"`，每 100ms 刷一次 |
| 对比两个解释器 | 系统 Python312 **没有 pywebview**；venv 有 |
| 实按 ESC（PostMessage 送 WM_KEYDOWN/UP，VK_ESCAPE） | 日志出现 `[界面] ESC：已打断当前播报`，`pythonw=6`，胶囊窗口在 |

---

## 三、四个确定性缺陷

### 缺陷 1：`_ensure_web_capsule` 只判断"对象存在"，不判断"进程活着"

```python
if self.web_capsule is not None:
    return True          # 子进程早死了，这里照样通过
```

于是 ESC 时走的顺序是：
隐藏全屏 → 隐藏悬浮球 → 往一个死进程发"显示胶囊" → **静默失败** → 屏幕上什么都不剩。

### 缺陷 2：`set_visible` / `_send` 静默失败，不返回结果

```python
if not self.conn:
    return               # 没连上就当作"发过了"
```

调用方无从知道"到底切过去了没有"，只能盲目往下走。

### 缺陷 3：子进程解释器不可信 → 秒退 → 界面 12 秒空白

子进程用 `sys.executable` 启动。双击 .py、IDE 里跑的时候它指向**没装 pywebview** 的解释器，子进程一启动就 `ModuleNotFoundError` 秒退。而 `srv.accept()` 是裸阻塞的，子进程一秒都没活过，却仍要**干等满 12 秒超时**——这段时间界面已经藏了、新的还没出来，Tk 主线程被占着，就是"卡死"。

### 缺陷 4：隐藏失败时窗口留在屏幕上，且 `terminate` 不回收 WebView2

`hide()` 发不出消息只是把 `alive=False`，窗口还在那儿，点什么都没反应。`stop()` 只 `proc.terminate()` 父进程，WebView2 是它下面独立的进程组，于是每退出一次就攒一批残留进程。

---

## 四、改了什么

| 文件 | 位置 | 改动 | 为什么 |
|---|---|---|---|
| `web_capsule.py` | 模块级 | 新增 `_taskkill_path()`、`_child_python()`、`_accept_alive()` | 定位 taskkill；子进程强制用带 pywebview 的解释器；子进程一死立刻失败 |
| `web_capsule.py` | `start()` | 改用 `_child_python()` 与 `_accept_alive()` | 不再盲信 `sys.executable`，不再干等 12 秒 |
| `web_capsule.py` | `set_visible()` | 返回 bool，异常置 `alive=False` 并返回 False | 让"送没送到"变成可判断的事实 |
| `web_capsule.py` | `stop()` | Windows 下 `taskkill /F /T /PID` | `/T` 连 WebView2 一起收，杜绝灰窗口残留 |
| `web_fullscreen.py` | 模块级 | 同上三个辅助函数 | 同上 |
| `web_fullscreen.py` | `_send()` | 返回 bool | 同上 |
| `web_fullscreen.py` | `hide()` | 发送失败 → 打印日志 → `stop()` 杀进程 → 返回 False | 绝不留"窗口还在但不响应"的假界面 |
| `web_fullscreen.py` | `stop()` | `taskkill /F /T /PID` | 回收 WebView2 |
| `web_fullscreen.py` | `_dispatch_event("collapse")` | 先 `jarvis.stop_speaking()`，再 `switch_to_orb` | ESC 第一语义是"别吵了" |
| `jarvis_gui.py` | 模块级 | 新增 `escape_to_orb(mgr)` | ESC 统一入口：先闭嘴，再收起，异常不吞 |
| `jarvis_gui.py` | `<Escape>` 绑定 | 改走 `escape_to_orb` | 同上 |
| `jarvis_gui.py` | `_ensure_web_capsule()` | 校验 `alive`，失效则 `stop()` 后重建 | 堵住缺陷 1 |
| `jarvis_gui.py` | `switch_to_orb` / `_switch_to_orb` / `_fallback_to_orb` | 包装异常 + 检查返回值 + 兜底悬浮球 | 不变量落地：失败一定退到可见载体 |

### 核心不变量

> **ESC 只允许"换载体"，不允许"消灭载体"。任何一次收起，结束状态都必须是"屏幕上有一个看得见、点得动的界面"。**

判断后续改动是否偏离，就看这一条。

---

## 五、验证

### 方法层（`probe_esc_fix.py`，7 项全 PASS）

| 场景 | 期望 | 结果 |
|---|---|---|
| A 一切正常 | 切到胶囊，悬浮球收起 | PASS `mode='web'` |
| B 胶囊显示送不出去 | 回退悬浮球 | PASS `mode='orb'` |
| C 全屏窗口僵死 | 杀掉子进程并回退 | PASS |
| D 收起过程抛异常 | 兜底悬浮球 | PASS |
| E 无连接时 `set_visible` | 返回 False（旧版静默 None） | PASS |
| F 无连接时 `_send` | 返回 False | PASS |
| G 子进程秒退 | 立刻发现（旧行为 12s） | PASS **0.20s** |

### 真实按键（PostMessage VK_ESCAPE 到全屏窗口）

```
[界面] ESC：已打断当前播报
pythonw=6（主进程存活）
Jarvis Capsule 窗口存在
```

---

## 六、还没解决的，而且更值得看

日志里反复出现这类记录：

```
[直接执行] 听到: 因为我现在就卡了。因为我因为很相信你。谢谢
[语音] 麦克风电平: 平均 1966 峰值 12581 (安静环境正常值 <200), 当前门限 2500
```

麦克风电平是正常值的 10~60 倍。在**免唤醒模式**下，程序把房间里所有说话都当成指令，逐句识别、逐句回复、逐句播报，形成正反馈。你说的"这太吵了"也在同一份日志里被它当成指令回复了一次。

这不是 ESC 的问题，是**另一条独立缺陷**：门限 2500 挡不住当前环境的底噪。可选方向：

1. 提高 RMS 门限到 3500~4000，并加"最短语音段 + 能量持续"双条件
2. 免唤醒模式下要求文本长度 ≥ 4 字且不含高置信噪声词
3. 增加一个"只说唤醒词才响应"的保守档位，默认开启

在 ESC 这一轮我没有动它——它改的是识别策略，风险面比"让界面别消失"大，需要你确认口径再动。
