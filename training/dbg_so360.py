# -*- coding: utf-8 -*-
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_tools  # noqa: E402

q = "明天合肥的天气怎么样"
print("search_so360 ->", len(web_tools.search_so360(q)))
r = requests.get("https://www.so.com/s?q=" + quote(q), headers=web_tools._HEADERS, timeout=12)
print("status", r.status_code, "len", len(r.text))
t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", r.text, flags=re.S | re.I)
chunks = re.split(r'<li class="res-list"', t)
print("chunks", len(chunks))
for c in chunks[1:5]:
    m = re.search(r'<h3[^>]*class="res-title"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', c, re.S)
    print("has_title", "res-title" in c, "regex_match", bool(m))
    i = c.find("res-title")
    if i >= 0:
        print(repr(c[i - 60:i + 420]))
