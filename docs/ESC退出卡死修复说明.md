# 按 ESC 会退出、会卡死 —— 根因与修复

修复日期：2026-11-19　分支：main　回滚：`_backup\esc-exit-fix-before-20261119`

> 本文分两轮。**第一轮的部分结论和验证方法是错的，已在第二轮推翻并修正**，保留在此以免后人重踩。

---

## 一、把"退出"拆开

| 形态 | 进程 | 屏幕 | 体感 |
|---|---|---|---|
| A 真退出 | 消失 | 无 | 程序没了 |
| B 假退出 | 还活着 | 什么都不剩 | "退出了" |
| C 冻住 | 还活着 | 窗口在，点哪都没反应 | "卡死了" |

三种共用同一条路径：ESC → 收起全屏 → 显示胶囊。

**不变量：ESC 只允许"换载体"，不允许"消灭载体"。任何时刻屏幕上必须有一个看得见、点得动的界面。**

---

## 二、第一轮修的（仍然有效）

| 缺陷 | 后果 | 修法 |
|---|---|---|
| `_ensure_web_capsule` 只判断对象存在，不看进程存活 | 子进程死了也返回 True，于是藏全屏+藏悬浮球+向死进程发显示 → 屏幕全空 | 校验 `alive`，失效则 stop 后重建 |
| `set_visible` / `_send` 静默失败无返回值 | 调用方不知道有没有切过去 | 改返回 bool |
| `hide()` 发不出消息只置 alive=False | 窗口留在屏幕上但不响应 | 发送失败直接结束子进程，由调用方兜底 |
| `switch_to_orb` 无兜底 | 中途失败就停在半路 | 拆包装+检查返回值+`_fallback_to_orb` |
| 子进程用 `sys.executable` | 系统 Python312 **没装 pywebview**，子进程秒退 | `_child_python()` 优先项目 venv |
| `srv.accept()` 裸阻塞 | 子进程秒退仍干等满 12 秒 | `_accept_alive` 轮询，**0.2s** 发现 |
| ESC 不打断播报 | 屏幕变了声音照旧，以为按键没生效 | 先 `stop_speaking()` 再收起 |

---

## 三、第一轮的错误（已推翻）

### 错误 1：把 `terminate()` 换成 `taskkill /F /T`，引入误杀风险

为了清 WebView2 残留，我把只杀目标进程的 `terminate()` 换成了盲目杀树。
`/T` 按 **PPID** 递归构树，而 Windows 的 PID 会回收复用：要清理的子进程已退出（PID 失效），
主进程的父进程（启动它的 shell）也早退出、PID 同样可能被复用。
两者一撞，主进程就可能被当成子树成员一起 `/F` 强杀。

**修正**：新建 `proc_tree.py`，用 psutil（校验 `create_time`，能识别 PID 复用），
并加硬护栏：**自己和自己全部祖先，永远不杀**。

### 错误 2：监控的是 venv redirector stub，不是真主进程

`.venv\Scripts\python.exe` 是个 **redirector**：它再启一个真正的解释器并等待。
`Start-Process -PassThru` 拿到的是 stub PID。我盯着 stub 看到"存活 70 秒 PASS"，
而真主进程早死了。证据：`[proc_tree] 自己=16080`，而我监控的是 29208。

**修正**：用 `Win32_Process.CommandLine` 匹配 `jarvis_gui.py` 且排除 `--child`，才是真主进程。

### 错误 3：把"按键注入失败"当成产品缺陷

`SendInput` 在本环境**返回 0**（被拒），`SetForegroundWindow` 也返回 False。
按键根本没送进去，却据此得出"ESC 没反应"的结论 —— 不成立。
另外 `IsWindowVisible` 对**最小化**窗口仍返回 True，必须用 `IsIconic` 判断收起。

**修正**：改用方法层驱动（假对象 + 真实方法）做确定性验证；真实按键由使用者完成。

---

## 四、第二轮新修的

| 文件 | 改动 | 依据 |
|---|---|---|
| `proc_tree.py`（新建） | psutil 精确杀树 + 自身/祖先护栏 + 诊断输出 | 替代危险的 `taskkill /T` |
| `web_capsule.py` / `web_fullscreen.py` `stop()` | 改用 `kill_tree`；**去掉 `poll() is None` 条件** | 子进程"已死"恰恰是最需要清残留的时候 |
| `lyric_subtitle.py` | 新增 `_win_ok()`，四个入口检查，`destroy` 置死 | 窗口失效后每 100ms 抛 `TclError`，日志被刷满、真线索被埋 |
| `jarvis_gui.py` | 新增全局 ESC 热键 `_start_esc_hotkey` / `_on_global_esc` / `_foreground_title` | 全屏 HUD 是 pywebview 子进程，Tk 的 `<Escape>` 绑定管不到它；原先只能靠页面 JS 收键，而 WebView2 焦点在渲染进程，不可靠 |
| `jarvis_gui.py` | `quit_app` 打调用栈；`run()` 打 mainloop 进出 + atexit | 进程"自己没了"时 stderr 是空的，必须留下退出路径证据 |
| `Jarvis.spec` | hiddenimports 补 `proc_tree` | 函数内 import，静态分析会漏 |

### 全局 ESC 的设计要点

- 用 `keyboard.add_hotkey("esc", ...)`，**不吞按键**（suppress 默认 False）
- 只在前台窗口标题含 `J.A.R.V.I.S` 或 `Jarvis` 时才接管，不干扰其它程序按 ESC
- 命中后 `root.after(0, escape_to_orb)`，回到 Tk 线程执行

---

## 五、验证

### 方法层 13 项全 PASS

`probe_esc_fix.py`（7）：正常切胶囊 / 胶囊送不出去 / 全屏僵死 / 抛异常 / 两个静默失败返回值 / 子进程秒退 0.20s 发现

`probe_esc_hotkey.py`（6）：全屏在前台→接管；胶囊在前台→接管；浏览器 / VS Code / 空标题 / 小写 jarvis 路径→不干预

### proc_tree 护栏实测

```
目标 pid=18308，其后代=[3672, 15036, 14924]
kill_tree 处理了 4 个进程；目标与全部后代均已结束
对自己调用 kill_tree 后仍然活着: True
对不存在的 PID 调用: 0
```

### 真实进程实测

- 真主进程（按命令行定位）存活 **120 秒**，`[退出] mainloop 开始` 有记录，无 quit_app、无 mainloop 返回
- **手动杀掉胶囊子进程**后，主进程存活 36 秒，日志出现 `[界面] Web 胶囊已恢复` —— 自愈链路打通
- `[界面] 全局 ESC 热键已注册` 确认写入日志

### 尚未验证（需使用者完成）

**真实按一次 ESC**。本环境 `SendInput` 被拒（返回 0），无法代按。
按下后 `jarvis-gui.log` 应出现：

```
[ESC] 全局热键触发，前台='...'
[界面] ESC：已打断当前播报        （若正在说话）
```

- 有这行但窗口没收起 → 问题在收起链路
- 连这行都没有 → `keyboard` 全局钩子在该环境没生效，需改用其它接管方式

---

## 六、另一条独立缺陷（未动）

免唤醒模式把环境杂音全当指令，逐句识别、逐句播报，形成正反馈。
日志实录：`[语音] 麦克风电平: 平均 1966 峰值 12581 (安静环境正常值 <200)`。
这与 ESC 无关，属于识别策略，需确认口径后再改。

---

## 七、架构级修复：唯一状态机 + 定期对账（2026-11-19）

### 为什么点层修复总是"补丁生补丁"

把历史版本翻了一遍（5 个 commit + 16 个备份 + 三份修复文档），量化结果：

- 全库 **165 处裸 `except:`**，分布在 22 个文件（jarvis.py 34、jarvis_gui 25、web_capsule 20、web_fullscreen 20）
- "界面现在应该长什么样"同时存在 **四本互不同步的账**：
  1. `self.mode`（orb / web / capsule / fullscreen）
  2. `self.ui_style`（web / new / classic，与 mode 语义重叠）
  3. 各载体对象的 `alive` / `visible` 标志
  4. Windows 里真实的窗口状态（可见 / 最小化）

四本账靠 socket、`after`、子进程异步消息同步，失败被 165 个裸 except 静默吞掉。
于是任何一处失败都汇成同一个症状：**屏幕上没有可交互的界面**，用户看到的就是"退出 / 卡死"。
每次在症状点补一层防线（加判断、加返回值、加兜底、加看门狗、加重启、加护栏），
防线本身又成了新的失败点——所以同一问题反复出现。

### 做法

新增 `ui_state.py`（114 行，独立可冒烟）：

| 组成 | 说明 |
|---|---|
| `top_windows()` / `find_windows()` / `on_screen()` | Win32 `EnumWindows` 实枚举，判断"在屏幕上"= 可见 **且非最小化**（`IsWindowVisible` 对最小化窗口仍返回 True，不能单用） |
| `restore_window(hwnd)` | `ShowWindow(SW_RESTORE)` + 置前。pywebview 的 `show()` 是 `SW_SHOW`，还原不了最小化窗口 |
| `UiStateMachine` | 唯一权威 `target` + `register(mode, probe, heal)` + `reconcile()` + 连续失败降级 |

`jarvis_gui.py` 接入：

- `__init__` 创建状态机，注册四个载体，`set_target(self.mode, "启动")`
- 四个切换入口（`use_web_ui` / `use_capsule_ui` / `use_classic_ui` / `switch_to_fullscreen` / `use_orb_ui` / `_switch_to_orb` / `_fallback_to_orb`）逐处 `set_target`；切换后 3 秒静默期，避免"正在切"被判成"切坏了"
- `_poll_ui` 每 20 tick（约 2 秒）调一次 `_reconcile_ui()`
- `quit_app` 先把 `ui_state.enabled = False`，退出过程不被对账"抢救"
- 目标形态连续 2 拍都不在屏幕上 → 自动降级到悬浮球，保证屏幕上永远有能点的东西

### 一个被自己踩到的坑（已修）

第一版 `heal()` 发完显示指令**立刻**复检窗口状态，结果：
`set_visible(True)` → 子进程还没处理完 → 复检 False → 两拍内直接降级。
真实日志：`[对账] web 不在屏幕上（连续 1 次）` → `（连续 2 次）` → `降级到悬浮球`。

修正两条：
1. `heal` 不再承担"判成功"的职责，**由下一拍的探测来判定**（`_fail_streak` 只在 probe 为 True 时清零）
2. `heal` 内部直接用 Win32 `SW_RESTORE` 同步把窗口拉回，不依赖异步消息

### 验证

| 项 | 结果 |
|---|---|
| `ui_state.py` 独立冒烟 | 缺失→自愈→下一拍恢复；连续 2 拍失败→降级，均符合预期 |
| `py_compile` + `import` | 通过（`ui_state`、`jarvis_gui`） |
| 真机启动 20 秒 | 只有 `[对账] 目标: orb -> web（启动）`，无循环自愈、无降级 |
| **破坏验证**：Win32 把 `Jarvis Capsule` 最小化 | 约 2 秒内出现 `[对账] web 不在屏幕上（连续 1 次），开始自愈` → `[对账] web 已恢复（此前连续 1 次不符）`，窗口 `vis=True min=False`，未降级 |
| 杀掉胶囊子进程 | 既有 `[界面] Web 胶囊已断开，正在重启…` 链路接管，对账无冲突 |

### 未做（下一步可选）

- 165 处裸 `except:` 收敛为"记日志 + 降级"，目前仍是静默吞掉
- 降级到悬浮球时给用户一个可见提示（现在只有日志）

