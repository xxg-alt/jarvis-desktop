# -*- coding: utf-8 -*-
"""多步任务结业考: 考规划器拆步骤的质量 (顺序/参数/核对步/危险步位置/不编造)。

用法:
  python training/run_exam_agent.py --url https://xxxx.trycloudflare.com/v1
判分基于 _agent_plan_from_llm 的原始步骤经 sanitize_plan 归一后的动作序列。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def norm_steps(raw_steps):
    from llm_brain import sanitize_plan
    out = []
    for s in raw_steps or []:
        if not isinstance(s, dict):
            continue
        plan = sanitize_plan({"action": str(s.get("action") or "chat"),
                              "args": s.get("args", {}), "say": s.get("say", "")})
        out.append({"action": plan.get("action", "chat"), "args": plan.get("args", {})})
    return out


def is_subseq(seq, actions):
    it = iter(actions)
    return all(any(a == want for a in it) for want in seq)


def grade(case, steps):
    actions = [s["action"] for s in steps]
    if not actions:
        return False, "没有返回步骤"
    if case.get("all_chat") and not all(a == "chat" for a in actions):
        return False, f"期望全部是 chat 步, 实际 {actions}"
    if case.get("exact_steps") and len(actions) != case["exact_steps"]:
        return False, f"期望 {case['exact_steps']} 步, 实际 {len(actions)} 步 {actions}"
    if case.get("first_in") and actions[0] not in case["first_in"]:
        return False, f"第一步期望 {case['first_in']} 之一, 实际 {actions[0]}"
    if case.get("seq") and not is_subseq(case["seq"], actions):
        return False, f"期望顺序 {case['seq']}, 实际 {actions}"
    for bad in case.get("forbid") or []:
        if bad in actions:
            return False, f"不该出现 {bad}, 实际 {actions}"
    if case.get("last") and actions[-1] != case["last"]:
        return False, f"最后一步期望 {case['last']}, 实际 {actions[-1]}"
    by_action = {}
    for s in steps:
        by_action.setdefault(s["action"], []).append(s)
    for chk in case.get("arg_checks") or []:
        group = by_action.get(chk["action"]) or []
        if len(group) <= chk["nth"]:
            return False, f"第 {chk['nth']} 个 {chk['action']} 不存在"
        got = str((group[chk["nth"]].get("args") or {}).get(chk["key"], ""))
        if "value" in chk and got != chk["value"]:
            return False, f"{chk['action']} 参数 {chk['key']} 期望 {chk['value']}, 实际 {got}"
        if "value_in" in chk and got not in chk["value_in"]:
            return False, f"{chk['action']} 参数 {chk['key']} 期望 {chk['value_in']} 之一, 实际 {got}"
    return True, " -> ".join(actions)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("JARVIS_EXAM_URL", ""))
    ap.add_argument("--provider", default="Kaggle 免费 35B")
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    args = ap.parse_args()
    if not args.url:
        print("缺 --url")
        return 2
    import jarvis
    jarvis.CONFIG.update({"ai_provider": args.provider, "ai_url": args.url,
                          "ai_model": args.model, "api_key": ""})
    cases = json.loads((ROOT / "training" / "exam_agent_cases.json").read_text(encoding="utf-8"))["cases"]
    wrong = []
    passed = 0
    for case in cases:
        raw = jarvis._agent_plan_from_llm(case["goal"])
        steps = norm_steps(raw.get("steps"))
        ok, why = grade(case, steps)
        print(f"[{'PASS' if ok else 'FAIL'}] #{case['id']} ({case['type']}) {case['goal']} -> {why}", flush=True)
        if ok:
            passed += 1
        else:
            wrong.append(case["id"])
    print(f"\n总分: {passed}/{len(cases)}" + (f", 错题: {wrong}" if wrong else ", 全对"))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
