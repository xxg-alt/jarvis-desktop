# -*- coding: utf-8 -*-
"""出门版 Cloudflare 隧道全打桩测试：假脚本 + 假查找 + 假启动，不联网。"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import remote_server
from tunnel_manager import TunnelManager, MISSING_MESSAGE

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def wait_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


tmp = tempfile.TemporaryDirectory()
fake_script = Path(tmp.name) / "cloudflared_fake.py"
fake_script.write_text('''\
import os
import sys
import time

style = os.environ.get("FAKE_TUNNEL_STYLE", "banner")
if style == "log":
    print("INF | quick tunnel: https://log-tunnel.trycloudflare.com | conns: 0", flush=True)
else:
    print("+----------------------------------------------+", flush=True)
    print("|  Your quick Tunnel has been created!         |", flush=True)
    print("|  https://banner-tunnel.trycloudflare.com     |", flush=True)
    print("+----------------------------------------------+", flush=True)
if os.environ.get("FAKE_TUNNEL_CRASH") == "1":
    sys.exit(7)
while True:
    time.sleep(0.05)
''', encoding="utf-8")


class StubPassword:
    def __init__(self, events, exists=True):
        self.events = events
        self.exists = exists

    def get(self):
        self.events.append("password")
        if not self.exists:
            self.exists = True
            return "generated-password-123456"
        return "existing-password-123456"


def make_popen(events, style="banner", crash=False):
    def popen(cmd, **kwargs):
        events.append(("spawn", list(cmd)))
        kwargs["env"] = dict(os.environ, FAKE_TUNNEL_STYLE=style,
                             FAKE_TUNNEL_CRASH="1" if crash else "0")
        return subprocess.Popen([sys.executable, str(fake_script)] + list(cmd[1:]), **kwargs)
    return popen


def make_manager(events, password, style="banner", crash=False, path=""):
    return TunnelManager(
        port=18765,
        password_getter=password.get,
        executable_path=path,
        which=lambda name: "fake-cloudflared-on-path",
        popen=make_popen(events, style=style, crash=crash),
        restart_delays=(0, 0),
        sleep=lambda seconds: None,
    )


try:
    events = []
    password = StubPassword(events, exists=False)
    manager = make_manager(events, password)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        check(manager.start() is True, "启用时应拉起隧道")
        check(wait_until(lambda: bool(manager.url)), "应从假脚本输出解析公网地址")
        check(manager.url == "https://banner-tunnel.trycloudflare.com",
              f"横幅框 URL 解析错误: {manager.url}")
        check(output.getvalue().count(manager.url) == 1, "公网地址只应在控制台打印一次")
    check(events[0] == "password", "隧道开启前必须先确认口令")
    check(len(events) >= 2 and events[1][0] == "spawn", "确认口令后才可启动子进程")
    check(password.exists is True, "口令不存在时应先生成")
    command = events[1][1]
    check(command[1:] == ["tunnel", "--url", "http://127.0.0.1:18765", "--no-autoupdate"],
          f"cloudflared 命令不符: {command}")
    check(command[0] == "fake-cloudflared-on-path", "未配置路径时应从 PATH 查找")
    process = manager._process
    check(process is not None and process.poll() is None, "假 cloudflared 应仍在运行")
    manager.stop()
    check(process is not None and process.poll() is not None, "退出时应终止子进程")
    check(manager.url == "", "停止后不应保留公网地址")

    for style, expected in (("banner", "https://banner-tunnel.trycloudflare.com"),
                            ("log", "https://log-tunnel.trycloudflare.com")):
        events = []
        manager = make_manager(events, StubPassword(events), style=style)
        try:
            check(manager.start() is True, f"{style} 格式应启动隧道")
            check(wait_until(lambda: manager.url == expected), f"{style} 格式 URL 应被解析")
        finally:
            manager.stop()

    events = []
    manager = TunnelManager(
        port=18765, password_getter=StubPassword(events).get, enabled=False,
        which=lambda name: "fake-cloudflared-on-path",
        popen=make_popen(events), restart_delays=(0, 0), sleep=lambda seconds: None,
    )
    check(manager.start() is False and not events, "remote_public_enabled=false 时不应启动隧道")
    check(manager.url == "", "disabled 时不应有公网地址")

    events = []
    missing = TunnelManager(
        port=18765, password_getter=StubPassword(events).get,
        which=lambda name: None, popen=make_popen(events),
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        check(missing.start() is False, "缺可执行文件应返回失败而不是抛异常")
    check(MISSING_MESSAGE == missing.error, "缺可执行文件应给出可读错误")
    check("Cloudflare 官网" in output.getvalue() and "PATH" in output.getvalue(),
          "缺可执行文件时应提示本机安装指引")
    check(not [item for item in events if item[0] == "spawn"], "缺可执行文件时不应启动子进程")

    events = []
    bad_path = TunnelManager(
        port=18765, password_getter=StubPassword(events).get,
        executable_path=str(Path(tmp.name) / "not-there.exe"),
        which=lambda name: "fake-cloudflared-on-path", popen=make_popen(events),
    )
    check(bad_path.start() is False and bad_path.error == MISSING_MESSAGE,
          "配置里的路径无效时应给出同样的指引")

    events = []
    crashing = make_manager(events, StubPassword(events), crash=True)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        crashing.start()
        check(wait_until(lambda: "连续退出 3 次" in crashing.error
                         and not crashing._thread.is_alive()), "连崩后应停止重启")
    spawns = [item for item in events if item[0] == "spawn"]
    check(len(spawns) == 3, f"首次 + 最多 2 次重启共应启动 3 次，实际 {len(spawns)}")
    check("已停止自动重启" in crashing.error, "停止重启时应有可读提示")
    check("已停止自动重启" in output.getvalue(), "停止重启提示应打印到本机控制台")

    class SpyTunnel:
        created = []

        def __init__(self, **kwargs):
            SpyTunnel.created.append(self)
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.error = ""

        @property
        def url(self):
            return "https://spy-tunnel.trycloudflare.com"

        def start(self):
            self.started = True
            return True

        def stop(self):
            self.stopped = True

    original_tunnel = remote_server.TunnelManager
    remote_server.TunnelManager = SpyTunnel
    service = remote_server.RemoteControlServer(
        config={"remote_enabled": True, "remote_port": 0, "remote_public_enabled": False},
        password_path=str(Path(tmp.name) / "service_password.txt"),
        register_speak_hook=False,
    )
    try:
        check(service.start() is True, "局域网服务应正常启动")
        check(not SpyTunnel.created, "未显式开启时不应创建隧道")
        service.config["remote_public_enabled"] = True
        check(service.start() is True, "开启公网配置后应可重新启动")
        check(len(SpyTunnel.created) == 1 and SpyTunnel.created[0].started,
              "开启后应启动隧道")
        check(SpyTunnel.created[0].kwargs["port"] == service.server.server_address[1],
              "隧道应只指向遥控服务的实际端口")
        check(service.public_url == "https://spy-tunnel.trycloudflare.com",
              "服务应能读到公网地址供本机菜单查看")
        service.config["remote_public_enabled"] = False
        check(service.start() is True, "关闭公网配置后应可重新启动")
        check(SpyTunnel.created[0].stopped is True, "再次启动时应停掉仍在跑的隧道")
        check(service.public_url == "", "关闭后不应再暴露公网地址")
        check(service.public_status == "未启用", "关闭后状态应显示未启用")
    finally:
        service.stop()
        remote_server.TunnelManager = original_tunnel

    defaults = remote_server.remote_config()
    check(defaults["remote_public_enabled"] is False, "公网隧道默认必须关闭")
    check(defaults["cloudflared_path"] == "", "cloudflared 路径默认为空")
finally:
    tmp.cleanup()

if fails:
    print("FAIL")
    for item in fails:
        print(" -", item)
    sys.exit(1)
print("PASS: Cloudflare 隧道全部打桩断言通过")
