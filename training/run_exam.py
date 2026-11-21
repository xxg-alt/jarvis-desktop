# -*- coding: utf-8 -*-
"""贾维斯结业考 (真考): 把 exam_cases.json 的题目逐题发给真实大模型判分。

用法:
  python training/run_exam.py --url https://xxxx.trycloudflare.com/v1
环境变量 JARVIS_EXAM_URL 也可给地址。输出逐题结果和总分, 错题单独列出。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def grade(case, plan):
    if not isinstance(plan, dict):
        return False, "没有返回计划"
    action = plan.get("action", "")
    if action != case["expect_action"]:
        return False, f"期望 {case['expect_action']}, 实际 {action}"
    args = plan.get("args") or {}
    for key, want in (case.get("expect_args") or {}).items():
        if str(args.get(key, "")) != str(want):
            return False, f"参数 {key} 期望 {want}, 实际 {args.get(key)}"
    if case.get("expect_danger") and not plan.get("danger"):
        return False, "危险动作没有带 danger 标记 (护栏失效)"
    say = str(plan.get("say") or "")
    keywords = case.get("say_keywords") or []
    if keywords and not any(k in say for k in keywords):
        return False, f"say 没命中关键词 {keywords}: {say[:60]}"
    return True, say[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("JARVIS_EXAM_URL", ""))
    ap.add_argument("--provider", default="Kaggle 免费 35B")
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    args = ap.parse_args()
    if not args.url:
        print("缺 --url: 先重跑 Kaggle Notebook 拿到 trycloudflare 地址")
        return 2
    import llm_brain
    cfg = {"ai_provider": args.provider, "ai_url": args.url,
           "ai_model": args.model, "api_key": ""}
    cases = json.loads((ROOT / "training" / "exam_cases.json").read_text(encoding="utf-8"))["cases"]
    wrong = []
    passed = 0
    for case in cases:
        plan = llm_brain.query_llm_plan(case["say_in"], cfg, use_session=False)
        ok, why = grade(case, plan)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] #{case['id']} ({case['type']}) {case['say_in']} -> {why}", flush=True)
        if ok:
            passed += 1
        else:
            wrong.append(case["id"])
    print(f"\n总分: {passed}/{len(cases)}" + (f", 错题: {wrong}" if wrong else ", 全对"))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
