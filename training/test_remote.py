# -*- coding: utf-8 -*-
"""手机遥控 v1 全打桩测试: 不触发任何真实桌面动作。"""
import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis
import remote_server

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def wait_until(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


commands = []
confirmations = []
alternate_entries = []
settled = threading.Event()

original_speak = jarvis.speak
original_handle = jarvis.handle_command
original_inner = jarvis._handle_command_inner
original_plan = jarvis.execute_plan


def fake_speak(text):
    jarvis._notify_speak(text)


def fake_handle_command(text, source=""):
    commands.append((text, source))
    if text in ("确定", "取消"):
        confirmations.append((text, source))
        fake_speak("已确认，继续执行" if text == "确定" else "已取消")
    settled.set()


def forbidden_entry(*args, **kwargs):
    alternate_entries.append((args, kwargs))


jarvis.speak = fake_speak
jarvis.handle_command = fake_handle_command
jarvis._handle_command_inner = forbidden_entry
jarvis.execute_plan = forbidden_entry

tmp = tempfile.TemporaryDirectory()
password_file = Path(tmp.name) / "remote_password.txt"
service = remote_server.RemoteControlServer(
    handle_command=fake_handle_command,
    config={"remote_enabled": True, "remote_port": 0},
    password_path=str(password_file),
)

try:
    check(service.start() is True, "服务应启动成功")
    check(service.server is not None and service.server.server_address[0] == "0.0.0.0",
          "remote_enabled 开时应绑定 0.0.0.0")
    port = service.server.server_address[1]
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()),
                  response.read().decode("utf-8"))
        conn.close()
        return result

    def post(path, payload, headers=None):
        return request("POST", path, json.dumps(payload, ensure_ascii=False),
                       dict({"Content-Type": "application/json"}, **(headers or {})))

    def feed_since(seq, headers=None):
        status, _, data = request("GET", f"/api/feed?since={seq}", headers=headers)
        check(status == 200, f"feed 应返回 200, 实际 {status}")
        return json.loads(data)

    password = service.reveal_password()
    check(len(password) >= 10, "首次启动应生成不少于 10 位口令")
    check(password_file.exists(), "口令文件应落在独立配置目录")
    check(password_file.stat().st_mode & 0o077 == 0, "口令文件权限不应开放给组/其他用户")

    status, headers, body = request("GET", "/remote")
    check(status == 302 and headers.get("Location") == "/login",
          "无 Cookie 访问 /remote 应跳登录")
    check(password not in body, "登录页不得出现口令")

    for path, method, payload in (
        ("/api/feed?since=0", "GET", None),
        ("/api/command", "POST", {"text": "打开记事本"}),
    ):
        if method == "GET":
            status, _, _ = request(method, path)
        else:
            status, _, _ = post(path, payload)
        check(status == 401, f"无 Cookie 访问 {path} 应 401, 实际 {status}")

    for i in range(4):
        status, _, _ = post("/api/login", {"password": "wrong-" + str(i)})
        check(status == 401, f"第 {i + 1} 次错口令应 401, 实际 {status}")
    status, _, _ = post("/api/login", {"password": "wrong-5"})
    check(status == 429, "连续 5 次错口令应 429")
    check(0 < service.auth.lock_until - time.time() <= 300.5, "锁定窗口应为 5 分钟")
    status, _, _ = post("/api/login", {"password": password})
    check(status == 429, "锁定期内正确口令也应 429")

    service.auth.lock_until = time.time() - 1
    status, headers, body = post("/api/login", {"password": password})
    check(status == 200, f"解锁后正确口令应登录成功, 实际 {status}")
    check(password not in body, "登录响应不得回显口令")
    set_cookie = headers.get("Set-Cookie", "")
    check("HttpOnly" in set_cookie and "Path=/" in set_cookie,
          "会话 Cookie 应为 HttpOnly 且限定路径")
    token = set_cookie.split(";", 1)[0].split("=", 1)[1]
    auth = {"Cookie": remote_server.COOKIE_NAME + "=" + token}

    status, _, body = request("GET", "/remote", headers=auth)
    check(status == 200 and "manifest" in body and "发送" in body,
          "已登录应拿到内联 manifest 的遥控页")
    check(password not in body, "遥控页不得出现口令")
    status, _, _ = request("GET", "/api/feed?since=0",
                           headers={"Cookie": remote_server.COOKIE_NAME + "=bad-token"})
    check(status == 401, "伪造 Cookie 应 401")
    status, headers, body = request("GET", "/api/login?password=" + password)
    check(status == 401 and "Set-Cookie" not in headers and '"ok"' not in body,
          "口令不得走 URL 参数登录")

    settled.clear()
    status, _, body = post("/api/command", {"text": "  打开记事本  "}, auth)
    check(status == 202, f"指令应被受理, 实际 {status}")
    check(wait_until(lambda: bool(commands)), "指令桩没有被调用")
    check(commands[0] == ("打开记事本", "remote"),
          f"指令应原样到达同一入口并标记 remote: {commands}")
    entries = feed_since(0, auth)
    check(any(e["who"] == "user" and e["text"] == "打开记事本" for e in entries),
          "用户指令应按序入列")

    fake_speak("第一句回复")
    fake_speak("第二句回复")
    entries = feed_since(0, auth)
    replies = [e["text"] for e in entries if e["who"] == "assistant"]
    check(replies == ["第一句回复", "第二句回复"], f"speak 回复应按序入列: {replies}")
    seqs = [e["seq"] for e in entries]
    check(seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "feed 序号必须严格递增")
    check(feed_since(seqs[-1], auth) == [], "since 只应返回更新的条目")
    check(all(password not in json.dumps(e, ensure_ascii=False) for e in entries),
          "feed 不得泄漏口令")

    settled.clear()
    post("/api/command", {"text": "关机"}, auth)
    check(wait_until(lambda: len(commands) >= 2), "危险指令桩没有被调用")
    post("/api/command", {"text": "确定"}, auth)
    check(wait_until(lambda: bool(confirmations)), "远程「确定」没走到确认桩")
    check(confirmations == [("确定", "remote")],
          f"确认必须由同一 handle_command 消费: {confirmations}")
    check(commands[-1] == ("确定", "remote"), "确认也应带 remote 来源")
    check(not alternate_entries, "不得直接调用 _handle_command_inner/execute_plan 第二入口")

    ring = remote_server.Feed(limit=100)
    for i in range(150):
        ring.add("assistant", str(i))
    capped = ring.since(0)
    check(len(capped) == 100 and capped[0]["text"] == "50" and capped[-1]["text"] == "149",
          "环形缓冲只保留最近 100 条")

    disabled = remote_server.RemoteControlServer(
        handle_command=fake_handle_command,
        config={"remote_enabled": False, "remote_port": 0},
        password_path=str(Path(tmp.name) / "disabled_password.txt"),
        register_speak_hook=False,
    )
    check(disabled.start() is False and disabled.server is None,
          "remote_enabled=false 时不应监听端口")
finally:
    service.stop()
    if "thread" in locals():
        thread.join(timeout=3)
    jarvis.speak = original_speak
    jarvis.handle_command = original_handle
    jarvis._handle_command_inner = original_inner
    jarvis.execute_plan = original_plan
    tmp.cleanup()

if fails:
    print("FAIL")
    for item in fails:
        print(" -", item)
    sys.exit(1)
print("PASS: 手机遥控 v1 全部打桩断言通过")
