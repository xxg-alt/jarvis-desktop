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
