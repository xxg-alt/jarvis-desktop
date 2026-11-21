# -*- coding: utf-8 -*-
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_tools  # noqa: E402
from llm_brain import _provider_chat, _LlmError  # noqa: E402

CFG = {"ai_provider": "Kaggle 免费 35B",
       "ai_url": "https://drawings-henderson-sons-procurement.trycloudflare.com/v1",
       "ai_model": "", "api_key": ""}

for q in ["明天合肥的天气怎么样", "ljj05356的手机号是多少", "现在黄金多少钱一克"]:
    print("=" * 20, q, flush=True)
    rs = web_tools.web_search(q)
    print("results:", len(rs), flush=True)
    for r in rs[:3]:
        print("-", r["title"][:36], "|", r["url"][:50], "| snip:", r["snippet"][:50], flush=True)
    if rs:
        body = web_tools.fetch_text(rs[0]["url"])
        print("fetch[0] chars:", len(body), "|", body[:120].replace("\n", " "), flush=True)
        parts = []
        for i, item in enumerate(rs, 1):
            parts.append(f"[{i}] {item['title']}\n{item['url']}\n{item['snippet']}")
        for item in rs[:2]:
            b = web_tools.fetch_text(item["url"])
            if b:
                parts.append(f"【网页正文: {item['title']}】\n{b}")
        ctx = "\n\n".join(parts)[:7000]
        try:
            ans = _provider_chat(CFG, [
                {"role": "system", "content": web_tools._WEB_SYSTEM},
                {"role": "user", "content": f"【问题】{q}\n\n【搜索结果】\n{ctx}"},
            ], max_tokens=600)
            print("LLM:", repr(ans[:220]), flush=True)
        except _LlmError as exc:
            print("LLM ERROR:", exc, flush=True)
    time.sleep(5)
