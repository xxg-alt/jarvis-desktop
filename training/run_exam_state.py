# -*- coding: utf-8 -*-
"""执行状态机结业考（平台无关）。

考的是"多步任务怎么跑"，不是"怎么动手"：用假执行器跑 agent_core.TaskRunner，
验权限闸门、停止、跳过、失败汇总、chat 不执行、写文件前传结果。
因为只依赖标准库，这个脚本可以在 Linux 云主机上跑，不需要 Windows/麦克风/界面。

用法:
  python training/run_exam_state.py            # 跑全部用例
  python training/run_exam_state.py --json out.jsonl
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_core


def build_runner(fail_actions, step_delay):
    """假执行器: 只记录与返回，不碰真实系统。"""
    calls = []
    composed = []

    def perform(step):
        calls.append(step)
        if step_delay:
            time.sleep(step_delay)
        if step.get("_fail") or step.get("action") in fail_actions:
            return False, f"{step.get('action')} 假失败", ""
        return True, f"已完成{step.get('action')}", "数据: 3 项"

    def persist(task):
        pass

    def notify(text, spoken=False):
        pass

    def pre_write(task, step, results_text):
        composed.append((step.get("args", {}).get("name"), results_text))
        step.setdefault("args", {})["content"] = "成稿:" + results_text

    runner = agent_core.TaskRunner(perform=perform, persist=persist, notify=notify,
                                   pre_write=pre_write)
    return runner, calls, composed


def run_case(case, t0):
    """按时间点触发 stop/skip，再等收工。返回 (task, calls, composed)。"""
    steps = [dict(s) for s in case["steps"]]
    task = agent_core.new_task(case["name"], steps)
    runner, calls, composed = build_runner(set(case.get("fail_actions") or []),
                                           float(case.get("step_delay") or 0))
    started_wall = time.time()
    runner.start(task)
    stop_at = case.get("stop_after")
    skip_at = case.get("skip_after")
    deadline = started_wall + float(case.get("timeout") or 30)
    while runner.busy() and time.time() < deadline:
        elapsed = time.time() - started_wall
        if stop_at is not None and elapsed >= float(stop_at):
            runner.stop()
            stop_at = None
        if skip_at is not None and elapsed >= float(skip_at):
            runner.skip()
            skip_at = None
        time.sleep(0.05)
    # 收工后再等一小会儿，确保 persist 已落终态
    time.sleep(0.2)
    return task, calls, composed


def check(case, task, calls, composed):
    exp = case.get("expect") or {}
    problems = []
    if exp.get("state") and task.get("state") != exp["state"]:
        problems.append(f"终态应为 {exp['state']}, 实际 {task.get('state')}")
    if exp.get("statuses"):
        actual = [s.get("status") for s in task.get("steps", [])]
        if actual != exp["statuses"]:
            problems.append(f"步骤状态应为 {exp['statuses']}, 实际 {actual}")
    if exp.get("calls") is not None and len(calls) != exp["calls"]:
        problems.append(f"执行次数应为 {exp['calls']}, 实际 {len(calls)}")
    if exp.get("max_calls") is not None and len(calls) > exp["max_calls"]:
        problems.append(f"执行次数应 ≤ {exp['max_calls']}, 实际 {len(calls)}")
    if exp.get("pre_write") is not None and len(composed) != exp["pre_write"]:
        problems.append(f"成稿钩子调用应为 {exp['pre_write']}, 实际 {len(composed)}")
    if exp.get("pre_write_has_data"):
        hit = any("数据" in (txt or "") or "已完成" in (txt or "") for _n, txt in composed)
        if not hit:
            problems.append("写文件步没有拿到前面步骤的结果文本")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="把逐题结果写成 JSONL")
    ap.add_argument("--cases", default=str(ROOT / "training" / "exam_state_cases.json"))
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    passed, failed = 0, []
    rows = []
    for case in cases:
        t0 = time.time()
        task, calls, composed = run_case(case, t0)
        problems = check(case, task, calls, composed)
        ok = not problems
        passed += 1 if ok else 0
        mark = "PASS" if ok else "FAIL"
        detail = "-" if ok else "; ".join(problems)
        print(f"[{mark}] {case['id']} {case['name']} -> {detail}", flush=True)
        rows.append({"id": case["id"], "name": case["name"], "ok": ok,
                     "problems": problems, "state": task.get("state"),
                     "statuses": [s.get("status") for s in task.get("steps", [])],
                     "calls": len(calls)})
        if not ok:
            failed.append(case["id"])

    print(f"\n总分: {passed}/{len(cases)}" + (f", 错题: {failed}" if failed else ", 全对"))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"明细已写入 {args.json}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
