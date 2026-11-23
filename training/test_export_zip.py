import os, sys, tempfile, time
from pathlib import Path

ROOT = Path(r"C:\Users\Ljj13\Desktop\jarvis-desktop-v1.2\jarvis-desktop-v1")
sys.path.insert(0, str(ROOT))

tmp = Path(tempfile.mkdtemp(prefix="jzip_"))
os.environ["JARVIS_CHATLOG_DIR"] = str(tmp)
import chat_log

chat_log.log_turn("把截图放到 D 盘的 截图 文件夹", "好的，已记录。", "set_screenshot_dir",
                  {"dir": "D:\\截图"}, "local", ts=time.time())
chat_log.log_turn("我的 key 是 sk-abc123456789012345678", "收到（已打码）。", "", None, "local",
                  ts=time.time())

out = Path(tempfile.mkdtemp(prefix="jzipout_")) / "打包.zip"
ok, msg = chat_log.export_encrypted_zip(str(out), "pw-测试-123")
print("导出:", ok, msg)
assert ok and out.exists(), "导出失败"

import pyzipper
with pyzipper.AESZipFile(out) as zf:
    names = zf.namelist()
    print("包内文件:", names)
    zf.setpassword(b"pw-\xe6\xb5\x8b\xe8\xaf\x95-123")
    for n in names:
        data = zf.read(n)
        assert data, f"{n} 空"
    print("AES 解密读取 OK, 共", len(names), "个文件")

try:
    with pyzipper.AESZipFile(out) as zf2:
        zf2.setpassword(b"wrong-pw")
        zf2.read(names[0])
    print("FAIL 错误密码竟然读出来了")
    sys.exit(1)
except Exception as exc:
    print("错误密码被正确拒绝:", type(exc).__name__)

# 包里内容确实是脱敏后的
with pyzipper.AESZipFile(out) as zf3:
    zf3.setpassword(b"pw-\xe6\xb5\x8b\xe8\xaf\x95-123")
    body = b"".join(zf3.read(n) for n in names).decode("utf-8", "replace")
assert "sk-abc" not in body and "sk-***" in body, "包内密钥未打码"
assert "C:\\Users" not in body, "包内家目录未脱敏"
print("包内已脱敏 OK")
print("结果: 全部通过")
