# -*- coding: utf-8 -*-
"""Agent 任务状态机（与平台无关）。

为什么要单独一个文件：
    多步任务的"怎么跑"（步骤顺序、权限闸门、停/跳过、挂起等待、结果传递）
    和"怎么动手"（taskkill、os.startfile、Word COM）是两件事。
    以前它们揉在 jarvis.py 里，后果是：想在 Linux 云主机上批量跑评测，
    必须先把整套 Windows 依赖搬过去 —— 做不到，于是评测只能手工点。

本文件的硬约束：
    1. 只允许 import 标准库。任何平台相关动作都通过注入回调完成；
    2. 不做 UI、不调大模型、不读磁盘（落盘也由 persist 回调负责）；
    3. 同一时刻只跑一个任务（真并发留给上层调度）。

注入接口（全部由调用方提供）：
    perform(step) -> (ok, result_text, tool_text)
        真正执行一步。ok 必须是布尔真值，不能靠话术字符串猜。
    persist(task)
        任务状态变了就落盘；每步之后都会调一次，崩溃也不丢进度。
    notify(text, spoken)
        spoken=False 只上字幕（进度不吵人）；spoken=True 才进播报队列。
    scope_of(step) -> "free" | "notify" | "ask"
        这一步属于哪级权限；默认读 step["scope"]。
    on_state(state)            可选：ACTING / IDLE 给界面用。
    verify(task, step, ok, text, tool_text) -> (ok, text, tool_text)
        可选：单步执行完、修复决策之前的回读钩子。只读核实, 不改状态机流程；
        抛异常时忽略, 保持原回读结论。
    guard_text(step) -> str
        可选：挂起确认话术里的具体内容（命令/路径）；默认读 guard_desc/say。
    perform 可接受可选的 task 关键字参数；提供时按任务来源执行，兼容单参实现。
    pre_write(task, step, results_text)  可选：写文件前的成稿钩子。
    repair(task, step, error_text) -> (fixed_step | None, reason)  可选：失败修复钩子。
        fixed_step 为 {action, args, say, scope, danger}；
        danger=True 时状态机只挂起等确认（scope 强制 ask + waiting），
        绝不直接执行；返回 None 表示修复未采用，reason 写进步骤结果。
        每步最多修一次：置 repair_attempted 并立即 persist 后才调 repair，
        修复调用前崩溃也不会丢标记。
"""

import inspect
import threading
import time

# 三级权限：越界才问，圈内直接干
SCOPE_FREE = "free"
SCOPE_NOTIFY = "notify"
SCOPE_ASK = "ask"

STATE_DRAFT = "draft"
STATE_APPROVED = "approved"
STATE_RUNNING = "running"
STATE_WAITING = "waiting_user"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

STATES = (STATE_DRAFT, STATE_APPROVED, STATE_RUNNING, STATE_WAITING,
          STATE_DONE, STATE_FAILED, STATE_CANCELLED)

MAX_STEPS = 8


def new_task(goal, steps, note=None):
    """构造一个 draft 任务。steps 里每项只需 action/args/say/scope。"""
    steps = [dict(s) for s in (steps or []) if isinstance(s, dict)][:MAX_STEPS]
    for s in steps:
        s.setdefault("status", "pending")
        s.setdefault("scope", SCOPE_FREE)
        s.setdefault("args", {})
        s.setdefault("say", "")
    task = {
        "goal": str(goal or "")[:200],
        "state": STATE_DRAFT,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "steps": steps,
    }
    if note:
        task["note"] = note
    return task


def task_progress_text(task, limit=3):
    """一句话进度汇总，给"任务进展"这类查询用。"""
    state = task.get("state")
    steps = task.get("steps", [])
    done_n = sum(1 for s in steps if s.get("status") == "done")
    total = len(steps)
    goal = str(task.get("goal", ""))[:40]
    if state == STATE_RUNNING:
        pending = [i for i, s in enumerate(steps) if s.get("status") == "pending"]
        where = f", 正在第 {pending[0] + 1} 步" if pending else ""
        return f"「{goal}」{done_n}/{total} 步{where}"
    if state == STATE_WAITING:
        return f"「{goal}」{done_n}/{total} 步, 等您确认"
    return f"「{goal}」{done_n}/{total} 步"


class TaskRunner:
    """单任务后台执行器：启线程跑步骤，支持停止/跳过/挂起等确认。"""

    def __init__(self, perform, persist, notify, scope_of=None,
                 on_state=None, pre_write=None, repair=None, verify=None,
                 guard_text=None):
        self._perform = perform
        self._persist = persist
        self._notify = notify
        self._scope_of = scope_of or (lambda step: step.get("scope") or SCOPE_FREE)
        self._guard_text = guard_text or (
            lambda step: str((step or {}).get("guard_desc")
                             or (step or {}).get("say")
                             or (step or {}).get("action") or ""))
        self._on_state = on_state
        self._pre_write = pre_write
        self._repair = repair
        self._verify = verify
        self._lock = threading.Lock()
        self._thread = None
        self._task = None
        self._stop = False
        self._skip = False
        self._gen = 0

    # ── 状态查询 ──
    def busy(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive())

    def task(self):
        return self._task

    @property
    def generation(self) -> int:
        return self._gen

    # ── 安全壳：回调出错不能把执行线程带崩 ──
    def _safe_persist(self, task):
        try:
            self._persist(task)
        except Exception as exc:
            print(f"[Agent] 任务落盘失败: {exc}")

    def _safe_notify(self, text, spoken=False):
        if not text:
            return
        try:
            self._notify(text, spoken)
        except Exception as exc:
            print(f"[Agent] 进度回调失败: {exc}")

    def _emit(self, state):
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:
            pass

    # ── 控制 ──
    def start(self, task):
        """起线程跑任务。返回 (是否启动, 说明)。"""
        with self._lock:
            if self.busy():
                return False, "已经有一个任务在跑了"
            self._gen += 1
            gen = self._gen
            self._stop = False
            self._skip = False
            self._task = task
            thread = threading.Thread(target=self._run, args=(task, gen),
                                      name="agent-task", daemon=True)
            self._thread = thread
            thread.start()
        return True, gen

    def stop(self) -> bool:
        """请求停止：当前这一步跑完就收工（步骤内部打断做不到）。"""
        with self._lock:
            if not self.busy():
                return False
            self._stop = True
            self._gen += 1
            task = self._task
            if task:
                task["state"] = STATE_CANCELLED
        if task:
            self._safe_persist(task)
        return True

    def skip(self) -> bool:
        with self._lock:
            if not self.busy():
                return False
            self._skip = True
        return True

    def _generation_changed(self, gen) -> bool:
        return self._gen != gen or self._stop

    def _call_perform(self, step, task):
        """把任务一起交给执行器（来源强绑定）；老的单参回调照旧可用。"""
        try:
            if "task" in inspect.signature(self._perform).parameters:
                return self._perform(step, task=task)
        except (TypeError, ValueError):
            pass
        return self._perform(step)

    def _step_guard_text(self, step) -> str:
        try:
            return str(self._guard_text(step) or "")
        except Exception:
            return str((step or {}).get("say") or (step or {}).get("action") or "")

    # ── 执行循环 ──
    def _run(self, task, gen):
        steps = task.get("steps", [])
        total = len(steps)
        task["state"] = STATE_RUNNING
        self._safe_persist(task)
        self._emit("ACTING")
        results_log = []
        repaired_nums = []
        try:
            for i, step in enumerate(steps):
                if self._generation_changed(gen):
                    task["state"] = STATE_CANCELLED
                    self._safe_persist(task)
                    self._safe_notify("任务已停止, Sir", spoken=True)
                    return
                if step.get("status") != "pending":
                    continue

                if self._scope_of(step) == SCOPE_ASK:
                    task["state"] = STATE_WAITING
                    step["status"] = "waiting"
                    self._safe_persist(task)
                    self._safe_notify(
                        f"任务「{task.get('goal', '')}」第 {i + 1}/{total} 步要动系统: "
                        f"{self._step_guard_text(step)}。"
                        "说「继续任务」放行, 说「停」中止",
                        spoken=True,
                    )
                    return

                if step.get("action") == "chat":
                    step["status"] = STATE_DONE
                    self._safe_persist(task)
                    continue

                if step.get("action") == "write_file" and results_log and self._pre_write:
                    try:
                        self._pre_write(task, step, "\n".join(results_log))
                    except Exception as exc:
                        print(f"[Agent] 成稿钩子失败, 用原草稿: {exc}")

                self._safe_notify(
                    f"第 {i + 1}/{total} 步 · {step.get('say') or step.get('action')}")

                try:
                    ok, text, tool_text = self._call_perform(step, task)
                    if self._verify is not None:
                        try:
                            ok, text, tool_text = self._verify(
                                task, step, ok, text, tool_text)
                        except Exception as exc:
                            print(f"[Agent 回读钩子失败] {exc}")
                except Exception as exc:
                    ok, text, tool_text = False, f"执行失败: {exc}", ""
                step["result"] = text
                step["status"] = STATE_DONE if bool(ok) else STATE_FAILED

                # 失败修复: 每步只给一次机会; 危险修正步只挂起等确认, 绝不直接执行
                if (not ok and self._repair is not None
                        and not step.get("repair_attempted")):
                    step["repair_attempted"] = True
                    self._safe_persist(task)   # 标记先落盘, 修复调用前崩溃也不丢
                    error_text = str(step.get("result") or "")
                    if tool_text:
                        error_text += f" | 数据: {tool_text}"
                    try:
                        fixed, reason = self._repair(task, step, error_text)
                    except Exception as exc:
                        fixed, reason = None, f"修复调用异常: {exc}"
                    if fixed and fixed.get("danger"):
                        step.update({"action": fixed.get("action"),
                                     "args": fixed.get("args") or {},
                                     "say": fixed.get("say") or "",
                                     "guard_desc": fixed.get("guard_desc") or "",
                                     "scope": SCOPE_ASK,
                                     "status": "waiting",
                                     "result": (step.get("result") or "") + "；修正方案待确认"})
                        task["state"] = STATE_WAITING
                        self._safe_persist(task)
                        self._safe_notify(
                            f"任务「{task.get('goal', '')}」第 {i + 1}/{total} 步失败, "
                            f"修正方案要动系统: {self._step_guard_text(step)}。"
                            "说「继续任务」放行, 说「停」中止",
                            spoken=True,
                        )
                        return
                    if fixed:
                        step.update({"action": fixed.get("action"),
                                     "args": fixed.get("args") or {},
                                     "say": fixed.get("say") or "",
                                     "guard_desc": fixed.get("guard_desc") or "",
                                     "scope": fixed.get("scope") or SCOPE_FREE})
                        step["status"] = "pending"
                        step["repaired"] = True
                        try:
                            ok, text, tool_text = self._call_perform(step, task)
                        except Exception as exc:
                            ok, text, tool_text = False, f"执行失败: {exc}", ""
                        step["result"] = text
                        step["status"] = STATE_DONE if bool(ok) else STATE_FAILED
                        if not ok:
                            step["result"] = f"修复后仍失败: {text}; 原错误: {error_text}"
                    else:
                        step["result"] = f"{step.get('result') or ''}；修复未采用: {reason}"

                if step.get("status") == STATE_DONE:
                    label = f"第{i + 1}步" + ("经修复完成" if step.get("repaired") else "")
                    entry = f"{label}({step.get('say') or step.get('action')}): {step.get('result', '')}"
                    if tool_text:
                        entry += f" | 数据: {tool_text}"
                    results_log.append(entry)
                    if step.get("repaired"):
                        repaired_nums.append(i + 1)
                self._safe_persist(task)

                if self._skip:
                    with self._lock:
                        self._skip = False
                    for rest in steps[i + 1:]:
                        if rest.get("status") == "pending":
                            rest["status"] = "skipped"
                    task["state"] = STATE_DONE
                    self._safe_persist(task)
                    self._safe_notify("已跳过剩余步骤, 任务收尾", spoken=True)
                    return

            failed = [i for i, s in enumerate(steps) if s.get("status") == STATE_FAILED]
            done_n = sum(1 for s in steps if s.get("status") == STATE_DONE)
            task["state"] = STATE_FAILED if failed and done_n == 0 else STATE_DONE
            self._safe_persist(task)
            fix_note = (("；" + "；".join(f"第{n}步经修复完成" for n in repaired_nums))
                        if repaired_nums else "")
            if failed:
                self._safe_notify(
                    f"任务「{task.get('goal', '')}」结束, 第 "
                    f"{'、'.join(str(i + 1) for i in failed)} 步没有成功{fix_note}", spoken=True)
            else:
                self._safe_notify(
                    f"任务「{task.get('goal', '')}」已完成, 共 {done_n} 步{fix_note}", spoken=True)
        except Exception as exc:
            print(f"[Agent 执行异常] {exc}")
            task["state"] = STATE_FAILED
            self._safe_persist(task)
            self._safe_notify(f"任务执行出错了: {exc}", spoken=True)
        finally:
            with self._lock:
                if self._task is task:
                    self._task = None
            self._emit("IDLE")
