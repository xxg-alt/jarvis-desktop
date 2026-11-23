# -*- coding: utf-8 -*-
"""手机遥控 v1: 局域网 HTTP 服务, 只依赖标准库。"""
import http.cookies
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tunnel_manager import TunnelManager

DEFAULT_PORT = 8765
FEED_LIMIT = 100
MAX_PASSWORD_FAILS = 5
LOCK_SECONDS = 300
MIN_PASSWORD_LEN = 10
COOKIE_NAME = "jarvis_remote_session"

_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
    '<rect width="192" height="192" rx="42" fill="#050e18"/>'
    '<circle cx="96" cy="96" r="70" fill="none" stroke="#00f7ff" stroke-width="8"/>'
    '<text x="96" y="126" font-size="96" font-family="sans-serif" '
    'text-anchor="middle" fill="#00f7ff">J</text></svg>'
)

LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#050e18">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>贾维斯遥控 · 登录</title>
<style>
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#050e18;color:#d8f7ff;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{display:flex;align-items:center;justify-content:center;padding:24px}
.card{width:100%;max-width:420px;background:#091726;border:1px solid #004766;border-radius:18px;
padding:28px 22px;box-shadow:0 0 40px rgba(0,247,255,.12)}
h1{margin:0 0 6px;font-size:24px;color:#00f7ff;letter-spacing:2px}
p{margin:0 0 22px;color:#73a2b5;font-size:14px}
label{display:block;margin-bottom:8px;font-size:14px;color:#8fd8e8}
input{width:100%;padding:16px;font-size:20px;border-radius:12px;border:1px solid #00a2cc;
background:#02060d;color:#fff;letter-spacing:2px}
button{width:100%;margin-top:16px;padding:16px;font-size:18px;border:0;border-radius:12px;
background:#00a2cc;color:#02060d;font-weight:700}
#hint{min-height:22px;margin-top:12px;color:#ff8e8e;font-size:14px}
</style></head><body>
<div class="card"><h1>JARVIS</h1><p>局域网遥控 · 请输入口令</p>
<form id="form"><label for="password">口令</label>
<input id="password" type="password" inputmode="text" autocomplete="current-password" autofocus>
<button type="submit">连接</button></form><div id="hint"></div></div>
<script>
const hint=document.getElementById('hint'),input=document.getElementById('password');
document.getElementById('form').addEventListener('submit',async function(e){
  e.preventDefault();hint.textContent='';
  const password=input.value;if(!password)return;
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:password}),credentials:'same-origin'});
    if(r.ok){input.value='';location.href='/remote';return}
    hint.textContent=r.status===429?'尝试次数过多，请 5 分钟后再试':'口令错误，请重试';
    input.value='';input.focus();
  }catch(err){hint.textContent='连接失败，请检查局域网'}
});
</script></body></html>"""

REMOTE_PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#050e18">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="贾维斯">
<link rel="manifest" href="__MANIFEST__">
<link rel="apple-touch-icon" href="__ICON__">
<title>贾维斯遥控</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#050e18;color:#d8f7ff;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{display:flex;flex-direction:column;padding:env(safe-area-inset-top) 12px env(safe-area-inset-bottom)}
header{flex:none;display:flex;align-items:center;justify-content:space-between;padding:12px 6px;
border-bottom:1px solid #004766}
header b{color:#00f7ff;letter-spacing:3px;font-size:18px}
header span{color:#73a2b5;font-size:12px}
#log{flex:1;overflow-y:auto;padding:16px 4px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:88%;padding:12px 14px;border-radius:16px;font-size:17px;line-height:1.5;
white-space:pre-wrap;word-break:break-word}
.msg.me{align-self:flex-end;background:#004766;color:#eaffff;border-bottom-right-radius:4px}
.msg.jarvis{align-self:flex-start;background:#091726;border:1px solid #004766;
border-bottom-left-radius:4px}
form{flex:none;display:flex;gap:10px;padding:10px 0 4px;border-top:1px solid #004766}
input{flex:1;min-width:0;padding:16px;font-size:18px;border-radius:14px;border:1px solid #00a2cc;
background:#02060d;color:#fff}
button{flex:none;padding:0 22px;font-size:18px;font-weight:700;border:0;border-radius:14px;
background:#00a2cc;color:#02060d}
</style></head><body>
<header><b>JARVIS</b><span id="state">局域网遥控</span></header>
<div id="log"></div>
<form id="form"><input id="text" placeholder="给贾维斯下指令…" autocomplete="off">
<button type="submit">发送</button></form>
<script>
const log=document.getElementById('log'),input=document.getElementById('text');
let lastSeq=0;
function render(items){
  for(const item of items){
    const row=document.createElement('div');
    row.className='msg '+(item.who==='user'?'me':'jarvis');
    row.textContent=item.text;log.appendChild(row);
    if(item.seq>lastSeq)lastSeq=item.seq;
  }
  if(items.length)log.scrollTop=log.scrollHeight;
}
async function poll(){
  try{
    const r=await fetch('/api/feed?since='+lastSeq,{credentials:'same-origin'});
    if(r.status===401){location.href='/login';return}
    if(r.ok)render(await r.json());
  }catch(err){document.getElementById('state').textContent='连接中断，重试中…'}
  setTimeout(poll,1000);
}
document.getElementById('form').addEventListener('submit',async function(e){
  e.preventDefault();const text=input.value.trim();if(!text)return;
  input.value='';
  try{
    const r=await fetch('/api/command',{method:'POST',
      headers:{'Content-Type':'application/json'},credentials:'same-origin',
      body:JSON.stringify({text:text})});
    if(r.status===401)location.href='/login';
    if(!r.ok)document.getElementById('state').textContent='指令未送达';
  }catch(err){input.value=text;document.getElementById('state').textContent='连接失败'}
});
poll();
</script></body></html>"""


def _uri(mime, text):
    return mime + "," + urllib.parse.quote(text, safe="")


def _remote_html():
    icon = _uri("data:image/svg+xml", _ICON_SVG)
    manifest = json.dumps({
        "name": "贾维斯遥控",
        "short_name": "贾维斯",
        "start_url": "/remote",
        "display": "standalone",
        "background_color": "#050e18",
        "theme_color": "#00f7ff",
        "icons": [{"src": icon, "sizes": "any", "type": "image/svg+xml"}],
    }, ensure_ascii=False, separators=(",", ":"))
    return (REMOTE_PAGE
            .replace("__MANIFEST__", _uri("data:application/manifest+json", manifest))
            .replace("__ICON__", icon))


def lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def default_password_path():
    try:
        import jarvis
        base = Path(jarvis.USER_CFG_FILE).parent
    except Exception:
        base = Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis"
    return base / "remote_password.txt"


def remote_config():
    try:
        import jarvis
        config = jarvis.CONFIG
    except Exception:
        config = {}
    return {
        "remote_port": config.get("remote_port", DEFAULT_PORT),
        "remote_enabled": config.get("remote_enabled", True),
        "remote_public_enabled": bool(config.get("remote_public_enabled", False)),
        "cloudflared_path": str(config.get("cloudflared_path", "") or ""),
    }


class Feed:
    def __init__(self, limit=FEED_LIMIT):
        self._limit = limit
        self._entries = deque(maxlen=limit)
        self._seq = 0
        self._lock = threading.Lock()

    def add(self, who, text):
        text = str(text or "").strip()
        if not text:
            return None
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "ts": time.time(),
                     "who": "user" if who == "user" else "assistant", "text": text}
            self._entries.append(entry)
            return entry

    def add_assistant(self, text):
        return self.add("assistant", text)

    def since(self, seq):
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            seq = 0
        with self._lock:
            return [dict(item) for item in self._entries if item["seq"] > seq]


class PasswordStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_password_path()

    def get(self):
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            if len(text) >= MIN_PASSWORD_LEN:
                return text
        except OSError:
            pass
        return self._write(secrets.token_hex(8))

    def reset(self):
        return self._write(secrets.token_hex(8))

    def _write(self, password):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(password + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return password


class AuthGate:
    def __init__(self, password_store):
        self.password = password_store
        self.fails = 0
        self.lock_until = 0.0
        self.sessions = set()
        self._lock = threading.Lock()

    def locked(self):
        with self._lock:
            return self.lock_until > time.time()

    def login(self, password):
        with self._lock:
            if self.lock_until > time.time():
                return "locked", None
            expected = self.password.get()
            if not isinstance(password, str) or not secrets.compare_digest(password, expected):
                self.fails += 1
                if self.fails >= MAX_PASSWORD_FAILS:
                    self.lock_until = time.time() + LOCK_SECONDS
                    self.fails = 0
                    return "locked", None
                return "bad", None
            self.fails = 0
            self.lock_until = 0.0
            token = secrets.token_urlsafe(24)
            self.sessions.add(token)
            return "ok", token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            return token in self.sessions

    def logout(self, token):
        with self._lock:
            self.sessions.discard(token)

    def clear_sessions(self):
        with self._lock:
            self.sessions.clear()


class RemoteControlServer:
    def __init__(self, handle_command=None, config=None, password_store=None,
                 password_path=None, feed=None, register_speak_hook=True):
        self.config = dict(remote_config() if config is None else config)
        self.handle_command = handle_command
        self.feed = feed if feed is not None else Feed()
        self.passwords = password_store or PasswordStore(password_path)
        self.auth = AuthGate(self.passwords)
        self._register_speak_hook = register_speak_hook
        self._speak_module = None
        self._previous_hook = None
        self._httpd = None
        self._serving = False
        self.url = ""
        self._tunnel = None

    @property
    def server(self):
        return self._httpd

    @property
    def public_url(self):
        return self._tunnel.url if self._tunnel is not None else ""

    @property
    def public_status(self):
        if not self.config.get("remote_public_enabled", False):
            return "未启用"
        if self.public_url:
            return "已就绪"
        if self._tunnel is not None and self._tunnel.error:
            return "异常"
        return "连接中"

    def start(self):
        self.stop_tunnel()
        self._close_httpd()
        if not self.config.get("remote_enabled", True):
            print("[手机遥控] 已在配置中关闭 (remote_enabled=false)", flush=True)
            return False
        port = int(self.config.get("remote_port", DEFAULT_PORT) or 0)
        try:
            self._httpd = _HttpServer(("0.0.0.0", port), self)
        except OSError as exc:
            self._httpd = None
            print(f"[手机遥控] 端口 {port} 启动失败: {type(exc).__name__}", flush=True)
            return False
        self.passwords.get()
        self._restore_speak_hook()
        self._install_speak_hook()
        self.url = "http://{}:{}/remote".format(lan_ip(), self._httpd.server_address[1])
        print(f"[手机遥控] 局域网地址: {self.url}", flush=True)
        print("[手机遥控] 口令可在托盘菜单「显示手机遥控口令」查看或重置", flush=True)
        if self.config.get("remote_public_enabled", False):
            try:
                self._tunnel = TunnelManager(
                    port=self._httpd.server_address[1],
                    password_getter=self.passwords.get,
                    enabled=True,
                    executable_path=self.config.get("cloudflared_path", ""),
                )
                self._tunnel.start()
            except Exception as exc:
                print(f"[出门遥控] 启动失败: {type(exc).__name__}: {exc}", flush=True)
                self._tunnel = None
        return True

    def stop_tunnel(self):
        if self._tunnel is None:
            return
        try:
            self._tunnel.stop()
        except Exception:
            pass
        self._tunnel = None

    def serve_forever(self):
        if self._httpd is not None:
            self._serving = True
            try:
                self._httpd.serve_forever(poll_interval=0.2)
            finally:
                self._serving = False

    def _close_httpd(self):
        if self._httpd is None:
            return
        if self._serving:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
        try:
            self._httpd.server_close()
        except Exception:
            pass
        self._httpd = None

    def stop(self):
        self.stop_tunnel()
        self._close_httpd()
        self._restore_speak_hook()

    def reveal_password(self):
        return self.passwords.get()

    def reset_password(self):
        password = self.passwords.reset()
        self.auth.clear_sessions()
        return password

    def dispatch(self, text):
        self.feed.add("user", text)
        target = self.handle_command
        if target is None:
            try:
                import jarvis
                target = jarvis.handle_command
            except Exception:
                return False

        def run():
            try:
                target(text, source="remote")
            except Exception as exc:
                print(f"[手机遥控] 指令执行失败: {type(exc).__name__}: {exc}", flush=True)

        threading.Thread(target=run, name="remote-command", daemon=True).start()
        return True

    def _install_speak_hook(self):
        if not self._register_speak_hook:
            return
        try:
            import jarvis
            self._speak_module = jarvis
            self._previous_hook = jarvis.ON_SPEAK_FEED
            jarvis.ON_SPEAK_FEED = self.feed.add_assistant
        except Exception as exc:
            print(f"[手机遥控] 回复订阅失败: {type(exc).__name__}", flush=True)

    def _restore_speak_hook(self):
        if self._speak_module is not None:
            try:
                self._speak_module.ON_SPEAK_FEED = self._previous_hook
            except Exception:
                pass
            self._speak_module = None


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service):
        self.service = service
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "JarvisRemote/1.0"

    @property
    def service(self):
        return self.server.service

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/remote":
            if not self._authorized():
                self._redirect("/login")
            else:
                self._html(200, _remote_html())
            return
        if path == "/login":
            self._html(200, LOGIN_PAGE)
            return
        if path.startswith("/api/"):
            if not self._authorized():
                self._unauthorized()
                return
            if path == "/api/feed":
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self._json(200, self.service.feed.since((query.get("since") or [0])[0]))
            else:
                self._json(404, {"error": "not found"})
            return
        if path == "/":
            self._redirect("/remote" if self._authorized() else "/login")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/login":
            self._login()
            return
        if not path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._unauthorized()
            return
        if path == "/api/command":
            body = self._body()
            text = str((body or {}).get("text") or "").strip()
            if not text or len(text) > 2000:
                self._json(400, {"error": "invalid text"})
                return
            self.service.dispatch(text)
            self._json(202, {"ok": True})
            return
        if path == "/api/logout":
            self.service.auth.logout(self._token())
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 8192:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _login(self):
        auth = self.service.auth
        if auth.locked():
            self._json(429, {"error": "too many attempts"})
            return
        password = self._body().get("password")
        result, token = auth.login(password)
        if result == "locked":
            self._json(429, {"error": "too many attempts"})
        elif result == "bad":
            self._json(401, {"error": "invalid password"})
        else:
            self._json(200, {"ok": True}, headers=[
                ("Set-Cookie", "{}={}; Path=/; HttpOnly; SameSite=Lax".format(COOKIE_NAME, token)),
            ])

    def _token(self):
        raw = self.headers.get("Cookie") or ""
        try:
            cookies = http.cookies.SimpleCookie()
            cookies.load(raw)
            return cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else ""
        except http.cookies.CookieError:
            return ""

    def _authorized(self):
        return self.service.auth.valid(self._token())

    def _unauthorized(self):
        self._json(401, {"error": "unauthorized"})

    def _redirect(self, location):
        self._reply(302, "", "text/plain; charset=utf-8", [("Location", location)])

    def _html(self, status, text):
        self._reply(status, text, "text/html; charset=utf-8")

    def _json(self, status, value, headers=None):
        self._reply(status, json.dumps(value, ensure_ascii=False),
                    "application/json; charset=utf-8", headers)

    def _reply(self, status, text, content_type, headers=None):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)
