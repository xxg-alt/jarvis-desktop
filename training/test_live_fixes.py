# -*- coding: utf-8 -*-
"""真项目实测回归(2026-11-24): 活大脑跑真项目挖出的 5 类问题, 逐个钉死。

来源: 七牛云活大脑(DeepSeek V4 Flash / Kimi K3)跑三个真实项目
(整理收件箱/汇总会议待办/查天气写文件)时暴露, 桩测全都没测出来:
1. 账本脱敏误判: 护栏话术带路径就给步打 redacted 标记 -> 批准恒被拒(死锁)
2. agent_continue 从最老等待任务找起: 僵尸等待任务挡住当前任务放行
3. move_file 只认单文件: 大脑把同类文件打包成 list src 时被判无效
4. sanitize 对 list src 的处理与护栏话术可读性
5. temperature 写死 0.7 被个别模型网关整单拒绝(Kimi K3 只收 0/0.6/1)
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis
import llm_brain
import system_tools


def test_redaction_flag_only_from_args():
    # (a) move_file 步: guard 话术带路径, args 完整 -> 绝不能变待重建
    data = {"tasks": [{"goal": "整理", "state": "waiting_user", "source": "local", "steps": [
        {"action": "move_file", "args": {"src": "/x/inbox/photo1.jpg", "dst": "/x/inbox/images"},
         "say": "移动 photo1.jpg", "guard_desc": "移动文件：/x/inbox/photo1.jpg → /x/inbox/images",
         "status": "pending", "scope": "ask"}]}]}
    out = jarvis._agent_redact_tasks(copy.deepcopy(data))
    jarvis._agent_recover_redacted(out)
    s = out["tasks"][0]["steps"][0]
    assert s["status"] != jarvis.AGENT_STEP_REBUILD, f"误判待重建: {s['status']}"
    assert s["args"]["dst"] == "/x/inbox/images", "args 被破坏"
    # (b) write_file 步: content 被抽离 -> 必须待重建(原保护不能丢)
    data2 = {"tasks": [{"goal": "写文件", "state": "waiting_user", "source": "local", "steps": [
        {"action": "write_file", "args": {"name": "a.txt", "content": "正文内容"},
         "say": "写 a.txt", "status": "pending", "scope": "notify"}]}]}
    out2 = jarvis._agent_redact_tasks(copy.deepcopy(data2))
    jarvis._agent_recover_redacted(out2)
    s2 = out2["tasks"][0]["steps"][0]
    assert s2["status"] == jarvis.AGENT_STEP_REBUILD, f"应待重建却没标: {s2['status']}"
    print("1. 脱敏标记只由 args 抽离触发 OK")


def test_agent_continue_newest_waiting():
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "agent_tasks.json"
    data = {"tasks": [
        {"goal": "僵尸旧任务", "state": "waiting_user", "source": "local",
         "steps": [{"action": "move_file", "args": {"src": "/a", "dst": "/b"},
                    "status": jarvis.AGENT_STEP_REBUILD}]},
        {"goal": "当前任务", "state": "waiting_user", "source": "local",
         "steps": [{"action": "move_file", "args": {"src": "/x/a.jpg", "dst": "/x/images"},
                    "status": "waiting", "source": "local", "scope": "ask"}]},
    ]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    old_path = jarvis.AGENT_TASKS_PATH
    old_start = jarvis._agent_start
    jarvis.AGENT_TASKS_PATH = path
    started = []
    jarvis._agent_start = lambda task: (started.append(task), {"say": "started"})[1]
    try:
        jarvis.agent_continue(source="local")
        assert started and started[0]["goal"] == "当前任务", started
        assert started[0]["steps"][0]["status"] == "pending"
        # 当前任务自己带待重建步时仍应拒绝
        data["tasks"][1]["steps"][0]["status"] = jarvis.AGENT_STEP_REBUILD
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        started.clear()
        res = jarvis.agent_continue(source="local")
        assert not started and "重建" in res.get("say", ""), res
    finally:
        jarvis.AGENT_TASKS_PATH = old_path
        jarvis._agent_start = old_start
    print("2. agent_continue 取最新等待任务 OK")


def test_sanitize_list_src():
    st = llm_brain.sanitize_plan({"action": "move_file",
                                  "args": {"src": ["/x/inbox/a.jpg", "/x/inbox/b.png"],
                                           "dst": "/x/inbox/images"},
                                  "say": "移图片"})
    assert st["action"] == "move_file", st
    assert isinstance(st["args"]["src"], list) and len(st["args"]["src"]) == 2, st
    assert "['" not in (st.get("guard_desc") or ""), st.get("guard_desc")
    st1 = llm_brain.sanitize_plan({"action": "move_file",
                                   "args": {"src": "/x/inbox/a.jpg", "dst": "/x/inbox/images"},
                                   "say": "移"})
    assert st1["args"]["src"] == "/x/inbox/a.jpg", st1
    print("3. sanitize list-src 保留 + 单文件不变 OK")


def test_batch_move():
    tmp = tempfile.mkdtemp()
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox)
    for n in ("a.jpg", "b.png"):
        open(os.path.join(inbox, n), "w").write("x")
    jarvis._file_tools.configure_file_roots([tmp])
    r = system_tools.move_file(src=[os.path.join(inbox, "a.jpg"), os.path.join(inbox, "b.png")],
                               dst=os.path.join(inbox, "images"))
    assert r["ok"], r
    assert sorted(os.listdir(os.path.join(inbox, "images"))) == ["a.jpg", "b.png"], r
    r2 = system_tools.move_file(src=[os.path.join(inbox, "images", "a.jpg"),
                                     os.path.join(inbox, "nope.jpg")],
                                dst=os.path.join(inbox, "misc"))
    assert not r2["ok"], r2
    print("4. move_file 批量移动 + 部分失败如实报 OK")


def test_batch_cap_and_mixed_roots():
    # 深审点名: 超过 50 个源路径绝不许静默截断 -> sanitize 整步拒绝
    many = [f"/x/inbox/f{i}.jpg" for i in range(51)]
    st = llm_brain.sanitize_plan({"action": "move_file",
                                  "args": {"src": many, "dst": "/x/inbox/images"},
                                  "say": "移"})
    assert st["action"] == "chat" and "50" in st["say"], st
    # 执行器兜底: 51 个真文件整批拒绝, 一个都不动
    tmp = tempfile.mkdtemp()
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox)
    names = [f"f{i}.jpg" for i in range(51)]
    for n in names:
        open(os.path.join(inbox, n), "w").write("x")
    jarvis._file_tools.configure_file_roots([tmp])
    r = system_tools.move_file(src=[os.path.join(inbox, n) for n in names],
                               dst=os.path.join(inbox, "images"))
    assert not r["ok"] and len(os.listdir(inbox)) == 51, r
    # 批次里混入授权根外的路径: 整体不如实报成功, 根外文件不许动
    outside = os.path.join(tempfile.mkdtemp(), "secret.txt")
    open(outside, "w").write("x")
    r2 = system_tools.move_file(src=[os.path.join(inbox, "f0.jpg"), outside],
                                dst=os.path.join(inbox, "images"))
    assert not r2["ok"] and os.path.exists(outside), r2
    print("6. 批量上限整批拒绝 + 混入根外路径不如实报成功 OK")


def test_temperature_retry():
    calls = []

    class FakeResp:
        def __init__(self, code, text="", data=None):
            self.status_code = code
            self.text = text
            self._data = data or {}

        def json(self):
            return self._data

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        if len(calls) == 1:
            return FakeResp(400, '{"error":{"message":"temperature must be 0, 0.6, or 1"}}')
        return FakeResp(200, "", {"choices": [{"message": {"content": "好的"}}], "usage": {}})

    old_post = llm_brain.requests.post
    llm_brain.requests.post = fake_post
    try:
        cfg = {"ai_provider": "Kaggle 免费 35B", "ai_url": "http://x/v1",
               "ai_model": "moonshotai/kimi-k3", "api_key": ""}
        out = llm_brain._provider_chat(cfg, [{"role": "user", "content": "hi"}])
        assert out == "好的", out
        assert "temperature" in calls[0] and "temperature" not in calls[1], calls
    finally:
        llm_brain.requests.post = old_post
    print("5. temperature 被拒自动去掉重试 OK")


if __name__ == "__main__":
    test_redaction_flag_only_from_args()
    test_agent_continue_newest_waiting()
    test_sanitize_list_src()
    test_batch_move()
    test_batch_cap_and_mixed_roots()
    test_temperature_retry()
    print("全部通过 (真项目实测回归: 脱敏误判/放行取最新/批量移动/temperature 兼容)")
