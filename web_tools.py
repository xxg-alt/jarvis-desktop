# -*- coding: utf-8 -*-
"""贾维斯真联网搜索工具 (训练科目: 真联网搜索)

流程: 搜索引擎拿结果 -> 抓前几条网页正文 -> 交给大模型按出处回答。
- 搜索源: Bing 国际版为主 (实测可直连、结果块稳定), DuckDuckGo HTML 兜底。
- 全程只用 requests + 标准库, 不加新依赖; 任何一步失败都返回空, 由调用方
  退回老行为 (打开百度搜索页), 绝不让主流程崩。
- 回答纪律: 只依据抓到的内容, 带出处, 查不到就直说, 不编造。
"""
import html as html_lib
import re
from urllib.parse import quote, urlsplit

import requests

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}
_SEARCH_TIMEOUT = 12
_FETCH_TIMEOUT = 8
_MAX_ANSWER_CHARS = 500

_SESSIONS = {}
_WARM_HOMES = {
    "www.bing.com": "https://www.bing.com/",
    "www.so.com": "https://www.so.com/",
}
_QUERY_CACHE = {}
_CACHE_TTL = 600
_ENGINE_FAILS = {}


def _fetch_page(url, timeout=_SEARCH_TIMEOUT):
    """带会话的抓取: 同一搜索引擎先访问首页拿 cookie 再搜, 否则容易被当机器人挡。
    返回 (status_code, text); 异常返回 (0, "")。"""
    from urllib.parse import urlsplit as _split
    host = _split(url).netloc
    session = _SESSIONS.get(host)
    if session is None:
        session = requests.Session()
        _SESSIONS[host] = session
        home = _WARM_HOMES.get(host)
        if home:
            try:
                session.get(home, headers=_HEADERS, timeout=8)
            except Exception:
                pass
    try:
        r = session.get(url, headers=_HEADERS, timeout=timeout)
        return r.status_code, r.text
    except Exception as exc:
        print(f"[联网搜索] 请求失败 {host}: {exc}")
        return 0, ""


def _clean(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def search_bing(query, limit=5):
    url = "https://www.bing.com/search?q=" + quote(query)
    status, text = _fetch_page(url)
    if status != 200:
        return []
    results = []
    for block in re.findall(r'<li class="b_algo".*?</li>', text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        results.append({
            "url": html_lib.unescape(m.group(1)),
            "title": _clean(m.group(2)),
            "snippet": _clean(snippet_m.group(1)) if snippet_m else "",
        })
        if len(results) >= limit:
            break
    return results


def search_ddg(query, limit=5):
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    status, text = _fetch_page(url)
    if status != 200:
        return []
    results = []
    for m in re.finditer(r'class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        results.append({"url": html_lib.unescape(m.group(1)),
                        "title": _clean(m.group(2)), "snippet": ""})
        if len(results) >= limit:
            break
    return results


def search_so360(query, limit=5):
    """360 搜索: 服务端渲染、带 data-mdurl 真实链接, 国内网络最稳。"""
    url = "https://www.so.com/s?q=" + quote(query)
    status, raw = _fetch_page(url)
    if status != 200:
        return []
    text = re.sub(r"<style.*?</style>|<script.*?</script>", " ", raw, flags=re.S | re.I)
    results = []
    # 不按 li 切块 (class 写法多变), 直接全局找标题链接, 摘要取其后一段
    pattern = r'<h3[^>]*class="res-title[^"]*"[^>]*>\s*<a([^>]+)>(.*?)</a>'
    for m in re.finditer(pattern, text, re.S):
        attrs, title_html = m.group(1), m.group(2)
        href = re.search(r'href="([^"]+)"', attrs)
        if not href:
            continue
        md = re.search(r'data-mdurl="([^"]+)"', attrs)
        real_url = html_lib.unescape(md.group(1) if md else href.group(1))
        seg = text[m.end():m.end() + 1500]
        desc = re.search(r'class="res-desc[^"]*"[^>]*>(.*?)</(?:p|div)>', seg, re.S)
        results.append({
            "url": real_url,
            "title": _clean(title_html),
            "snippet": _clean(desc.group(1)) if desc else "",
        })
        if len(results) >= limit:
            break
    return results


def search_sogou(query, limit=5):
    """搜狗: 服务端渲染, 标题链接多为直达; 360/Bing 被限流时的后备源。"""
    url = "https://www.sogou.com/web?query=" + quote(query)
    status, raw = _fetch_page(url)
    if status != 200 or "vr-title" not in raw:
        return []  # 验证码页/异常页没有结果标题, 直接判无结果
    text = re.sub(r"<style.*?</style>|<script.*?</script>", " ", raw, flags=re.S | re.I)
    results = []
    pattern = r'<h3[^>]*class="vr-title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    for m in re.finditer(pattern, text, re.S):
        seg = text[m.end():m.end() + 1500]
        desc = re.search(r'class="str-text-info[^"]*"[^>]*>(.*?)</p>', seg, re.S)
        if not desc:
            desc = re.search(r'class="fz-mid[^"]*"[^>]*>(.*?)</div>', seg, re.S)
        results.append({
            "url": html_lib.unescape(m.group(1)),
            "title": _clean(m.group(2)),
            "snippet": _clean(desc.group(1)) if desc else "",
        })
        if len(results) >= limit:
            break
    return results


def web_search(query, limit=5):
    """Bing(带会话) -> 360 -> DDG, 谁先出结果用谁; 同一问题 10 分钟内走缓存,
    避免短时间重复请求把搜索源惹毛 (360 连查会给验证码页)。"""
    import time
    key = (query, limit)
    hit = _QUERY_CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    now = time.time()
    for finder in (search_bing, search_so360, search_sogou, search_ddg):
        fails, last_fail = _ENGINE_FAILS.get(finder.__name__, (0, 0))
        if fails >= 2 and now - last_fail < 600:
            continue  # 熔断 10 分钟: 该源刚被限流, 别火上浇油; 过期自动再试
        try:
            found = finder(query, limit)
        except Exception as exc:
            print(f"[联网搜索] {finder.__name__} 失败: {exc}")
            found = []
        if found:
            _ENGINE_FAILS[finder.__name__] = (0, 0)
            if len(_QUERY_CACHE) > 50:
                _QUERY_CACHE.clear()
            _QUERY_CACHE[key] = (now, found)
            return found
        _ENGINE_FAILS[finder.__name__] = (fails + 1, now)
    return []


def fetch_text(url, limit=2500):
    """抓网页正文纯文本 (去脚本/样式/标签), 失败返回空串。"""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_FETCH_TIMEOUT)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", "text/html"):
            return ""
        text = r.text
    except Exception as exc:
        print(f"[联网搜索] 抓取失败 {url}: {exc}")
        return ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = _clean(text)
    return text[:limit]


_WEB_SYSTEM = (
    "你是桌面语音助手贾维斯的联网检索回答器。只依据用户消息里的【搜索结果】回答问题:\n"
    "1. 用中文回答, 先给答案本身, 默认 1~3 句, 能朗读;\n"
    "2. 涉及数字、日期、价格必须来自搜索结果, 结果里没有就直说没查到, 绝不编造;\n"
    "3. 回答末尾带一句出处, 格式: (来源: 网站名或域名);\n"
    "4. 搜索结果互相矛盾时, 优先更新、更权威的一条, 并说明有出入。"
)


def search_and_answer(query, config):
    """搜 + 读 + 答一条龙。返回可直接朗读的答案; 搜不到返回空串。"""
    query = str(query or "").strip()
    if not query:
        return ""
    if "天气" in query:
        try:
            from weather_tools import answer_weather
            weather_answer = answer_weather(query)
            if weather_answer:
                return weather_answer
        except Exception:
            pass
    results = web_search(query)
    if not results:
        return ""
    parts = []
    for i, item in enumerate(results, 1):
        parts.append(f"[{i}] {item['title']}\n{item['url']}\n{item['snippet']}")
    for item in results[:1]:
        body = fetch_text(item["url"])
        if body:
            parts.append(f"【网页正文: {item['title']}】\n{body}")
    context = "\n\n".join(parts)[:7000]
    from llm_brain import _provider_chat, _LlmError
    messages = [
        {"role": "system", "content": _WEB_SYSTEM},
        {"role": "user", "content": f"【问题】{query}\n\n【搜索结果】\n{context}"},
    ]
    try:
        answer = _provider_chat(config, messages, max_tokens=600)
    except _LlmError as exc:
        print(f"[联网搜索] 模型回答失败: {exc}")
        return ""
    answer = re.sub(r"\s+", " ", str(answer or "")).strip()
    if not answer:
        return ""
    if "来源" not in answer:
        host = urlsplit(results[0]["url"]).netloc or results[0]["title"]
        answer += f" (来源: {host})"
    return answer[:_MAX_ANSWER_CHARS]
