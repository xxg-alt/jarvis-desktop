# 失败修复 + 结果回读加厚 实施记录

## 改了哪些文件

- `llm_brain.py`：新增 `repair_step(goal, step, error_text, config)`（失败步交给模型重出一步，返回原始 dict 或 None）和固定 system 指令 `_REPAIR_STEP_SYSTEM`。复用已有 `_provider_chat` / `_strip_json_text`，不碰 `query_llm_plan` / `free_chat` / `parse_command`。
- `jarvis.py`：
  - 新增 `_agent_execute_step`（单步执行+回读成败）、`_agent_repair_step`（修正步过 `sanitize_plan` + `agent_scope_of` 校验）；
  - `_agent_loop` 增加失败修复分支（每步最多一次、危险修正步挂起确认、汇总注明）；
  - `_tool_data_text` 扩展为按动作产出结构化文本，并补 `_tool_items_text` / `_opened_app_path` / `_remaining_process_count`；
  - `_perform_plan` 给 open_app / close_app 补上成败位与结构化回读（open_app 的成败位只在 agent 上下文里写，直接指令播报行为不变）。
- `training/test_repair.py`：新增桩测。
- `training/repair_notes.md`：本文件。

## 修复流程

1. 某步 `_TL.action_ok=False`（含 open_app 启动失败、file_manage/inspect_system 返回 ok=False）或 `_perform_plan` 抛异常 → 记为 failed。
2. 若该步没有 `repair_attempted` 标记，置位后组装任务目标 + 失败步的 action/args/say + 失败结果文本（含结构化数据回读），调 `llm_brain.repair_step`。
3. 修正步先过 `sanitize_plan`（白名单+参数校验），降级成 chat 视为修复未采用；再过 `agent_scope_of`：danger 或作用域 ask 的修正步只写入 step 并把任务置 `waiting_user`，沿用现有的「说继续任务放行 / 说停中止」护栏，绝不直接调用 `_perform_plan`。
4. 普通修正步就地替换 action/args/say/scope 后重试一次；成功记 `repaired=True`，结果汇总和播报里注明「第N步经修复完成」；仍失败保留 failed 并写明原错误，按原有逻辑继续/结束。
5. `repair_attempted` 写回 step 并持久化，`agent_continue` 续跑后不会二次修复。

## 测试结果

- `python3 -m compileall -q .`：全部 .py 通过。
- `python3 training/test_chat_channel.py`：PASS（17 句分流 + free_chat 桩测全过）。
- `python3 training/test_repair.py`：PASS（正常修复只调一次模型；危险修正步只挂起不执行；续跑不再修复；修复仍失败如实记录；结果回读字段齐全）。
- 本地规则四条：打开记事本→open_app、现在几点→chat、关机→shutdown、音量调大→volume，结果不变。

## 拿不准的点

- 「护栏确认机制」选的是 agent 原有的 `ask → waiting_user → agent_continue` 流程，而不是直接指令的 `_guard_confirm`；这样不改护栏语义，但危险修正步要由用户再说一次「继续任务」才执行。
- close_app 的同名进程数只是尽力回读（psutil，`taskkill` 异步发出后可能还没退出）；读不到写「无法核验」，不假装成功。这一项没有改动播报话术。
- 修正步若被模型主动改成 `chat`，按「未采用修复」处理并如实记 failed，没有把它当成一次成功的修复。

## 审查修复轮

### FIX-1 close_app 把 taskkill 失败当成功
- 改法：`jarvis.py` close_app 分支改为 `subprocess.run(["taskkill","/im",exe,"/f"], capture_output=True,text=True)`，检查 `returncode`；非零时 `_TL.action_ok=False`，记录 `taskkill返回码/错误` 到 reason，回读 summary 用失败原因。exe 为空直接失败并写“缺少进程名”。回读始终保留 `remaining`（先/后尽力取，读不到为 `无法核验`），并在 `_tool_data_text` close_app 中输出 `原因=`。
- 测试：`training/test_repair.py` 新增桩测，mock taskkill 返回码 1 -> `action_ok=False`，回读含 `成功=否` 且含剩余/无法核验语义。

### FIX-2 system_tools 导入失败被记为成功
- 改法：`file_manage/inspect_system/run_powershell` 的 `from system_tools import ...` except 中设置 `_TL.action_ok=False`，并写 `_TL.last_tool_text = _tool_data_text(action, {"ok": False, "summary": f"系统工具不可用: {exc}", "data": {"action": action, "reason": ...}})`，保证 agent 循环能触发失败修复。
- 测试：桩测 mock `__import__` 对 system_tools 抛 ImportError -> `action_ok=False` 且回读含 `成功=否` 与 `系统工具不可用`。

### FIX-3 open_app 回读把“仅尝试”说成成功
- 改法：`smart_launcher.smart_launch_app` 返回三元 `(ok, msg, verify)`，确认启动路径返回 `"ok"`，裸名 os.startfile 兜底返回 `"unverified"`，未找到返回 `"fail"`。`jarvis.py` open_app 分支记录 `verify/reason`：仅尝试启动标 `unverified + 仅尝试启动，未核实`；启动不明/失败但回退打开同名文档时标 `unverified + 实际打开的是同名文档`。agent 上下文里 `_TL.action_ok` 仅在 `verify=="ok"` 时为真。`_tool_data_text` open_app 对 `unverified` 输出 `成功=无法核验`，路径标 `尝试路径=`，附 `原因=`。`_multi_open_say` 兼容四元结果，未核验项不报“已为您启动”。未改直接指令播报文案。
- 测试：桩测覆盖“回退开文档”和“仅尝试启动”两种路径，断言回读含 `无法核验` 且不含 `成功=是`。

### FIX-4 _tool_data_text 的 ok 默认值
- 改法：仅在 `_tool_data_text` 内部处理缺省 ok。`"ok" not in res` 时 `ok_text="无法核验"`；支持 `ok==True/"ok"`、`False/"fail"`、`"unverified"`，其他未知值也按无法核验处理，不再默认成功。
- 测试：桩测传入缺 ok 的 res，断言回读含 `无法核验` 且不含 `成功=是/否`。

### FIX-5 回归桩测
- 改法：在 `training/test_repair.py` 末尾追加审查回归段，恢复真实 `_perform_plan` 后执行 FIX-1~4 桩测；保留原有修复流程 4 项断言。
- 测试结果：`python3 training/test_repair.py` PASS（含“审查回归”）。

## 本轮验证
- `python3 -m py_compile jarvis.py smart_launcher.py training/test_repair.py training/test_chat_channel.py`：通过（smart_launcher.py 有既有 SyntaxWarning，非本轮引入）。
- `python3 training/test_chat_channel.py`：PASS。
- `python3 training/test_repair.py`：PASS。
- 本地规则四条回归：打开记事本→open_app、现在几点→chat、关机→shutdown、音量调大→volume，结果不变。

## 第二轮审查修复

### R2-1 open_app 异常路径回读仍谎报成功
- 改法：新增 `_open_app_verify_status(action_ok)`，成败位为 False 时把预置的 `open_app_verify="ok"` 纠正为 `fail`；open_app 最终回读与直接指令多开汇总都走这一处判断，异常路径回读只可能是 `成功=否/无法核验`，不再出现 `成功=是`。
- 测试：桩测把 `smart_launch_app` 换成抛异常的桩，断言 `action_ok=False` 且回读含 `成功=否`、不含 `成功=是`。

### R2-2 直接指令多开未核实也播报成功
- 改法：直接指令多开结果改为四元组，把 `_TL.open_app_verify` 带进汇总；`_multi_open_say` 对 `unverified` 统一输出「已尝试，未核实（原话）」，失败项仍单独说清，只有确认成功才进「已为您启动…」。其他播报未动。
- 测试：桩测直接走 `_handle_command_inner("同时打开微信和QQ", ...)`，启动器返回 unverified，断言播报含「已尝试」「未核实」且不含「已为您启动」。

### R2-3 smart_launcher.py 契约文档过期
- 改法：`smart_launch_app` 标注改为 `tuple[bool, str, str]`，docstring 写明三元含义及 `ok/unverified/fail` 三种核验状态。全仓 grep `smart_launch_app`：jarvis.py 两处调用与 test_repair.py 桩均为三元解包，没有二元组解包点。
- 测试：随全量 py_compile 与 test_repair.py 通过。

### R2-4 close_app 以 taskkill 返回码定成败、与进程残留矛盾
- 改法：去掉动作前的进程数预读；taskkill 后读到剩余数 > 0 时按未关干净处理——`action_ok=False`、回读 `成功=否`，并在原因/结果里注明「仍有 N 个同名进程在运行」；剩余数读不到时才沿用 taskkill 返回码定成败，并在原因中注明「同名进程剩余数无法核验, 未核实是否关闭」，回读保留 `同名进程剩余=无法核验`。
- 测试：桩测 mock taskkill 返回 0 + 剩余 2 → 失败且含残留说明；返回 0 + 剩余数读不到 → 沿用返回码成功且注明未核实。

### R2-5 repair_attempted 持久化窗口
- 改法：`_agent_loop` 置 `step["repair_attempted"]=True` 后立即 `_agent_persist(task)`，再组装错误文本并调修复模型；修复调用前崩溃重载不会丢标记。
- 测试：桩测用内存任务文件并在 `_agent_repair_step` 前抛异常模拟崩溃：修复调用发生时的落盘快照已含 `repair_attempted=true`，重新 `_agent_load_tasks` 后标记仍在。

### 本轮自跑日志（2026-11-21）
- `python3 -m compileall -q .`：`COMPILEALL_OK`（smart_launcher.py 第 9 行 `\,`、第 84 行 `\W` 两处既有 SyntaxWarning，非本轮引入）。
- `python3 training/test_repair.py`：PASS（修复最多一次、危险修正步走护栏、失败如实记录、结果回读加厚+两轮审查回归）。
- `python3 training/test_chat_channel.py`：PASS（17 句分流 + free_chat 桩测全过）。
- 本地规则四条：`打开记事本→open_app、现在几点→chat、关机→shutdown、音量调大→volume`，结果不变。
