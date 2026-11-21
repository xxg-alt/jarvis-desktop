# -*- coding: utf-8 -*-
"""真联网搜索结业考: 真搜真答, 按关键词/正则/出处/拒编判分。

用法:
  python training/run_exam_web.py --url https://xxxx.trycloudflare.com/v1
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def grade(case, answer):
    if not answer:
        return False, "没有返回答案 (搜索或回答失败)"
    if case.get("forbid_regex") and re.search(case["forbid_regex"], answer):
        return False, "编造了不该出现的内容 (命中禁用正则)"
    if case.get("regex") and not re.search(case["regex"], answer):
        return False, f"没命中期望正则 {case['regex']}"
    keywords = case.get("any_keywords") or []
    if keywords:
        hits = sum(1 for k in keywords if k in answer)
        if hits < case.get("min_hits", 1):
            return False, f"关键词只命中 {hits} 个: {keywords}"
    refusal = case.get("refusal_keywords") or []
    if refusal and not any(k in answer for k in refusal):
        return False, "没有表达查不到/不能提供"
    if case.get("need_citation") and "来源" not in answer:
        return False, "没有带出处"
    return True, answer[:70]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("JARVIS_EXAM_URL", ""))
    ap.add_argument("--provider", default="Kaggle 免费 35B")
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    args = ap.parse_args()
    if not args.url:
        print("缺 --url")
        return 2
    import web_tools
    cfg = {"ai_provider": args.provider, "ai_url": args.url,
           "ai_model": args.model, "api_key": ""}
    cases = json.loads((ROOT / "training" / "exam_web_cases.json").read_text(encoding="utf-8"))["cases"]
    wrong = []
    passed = 0
    for case in cases:
        answer = web_tools.search_and_answer(case["query"], cfg)
        ok, why = grade(case, answer)
        print(f"[{'PASS' if ok else 'FAIL'}] #{case['id']} ({case['type']}) {case['query']} -> {why}", flush=True)
        if ok:
            passed += 1
        else:
            wrong.append(case["id"])
    print(f"\n总分: {passed}/{len(cases)}" + (f", 错题: {wrong}" if wrong else ", 全对"))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
