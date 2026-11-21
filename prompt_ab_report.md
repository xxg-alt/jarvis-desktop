# Prompt A/B 对照报告  2026-11-20 17:09
模型: deepseek-chat  temperature: 0.7  无记忆上下文

| 口令 | 期望 | A组结果 | B组结果 |
|---|---|---|---|
| 打开微信 | open_app | OK open_app {"exe": "WeChat.exe", "name": "微信"} (1.0s) | OK open_app {"exe": "WeChat.exe", "name": "微信"} (1.1s) |
| 打开豆包 | open_app | OK open_app {"exe": "Doubao.exe", "name": "豆包"} (0.8s) | OK open_app {"exe": "doubao.exe", "name": "豆包"} (0.7s) |
| 帮我把音量调大一点 | volume | OK volume {"mode": "up", "steps": 5} (1.1s) | OK volume {"mode": "up", "steps": 5} (1.4s) |
| 下载文件夹里有什么PDF文件 | file_manage | OK file_manage {"action": "search", "pattern": "*.pdf"} (1.6s) | OK file_manage {"action": "search", "pattern": "*.pdf", "folder": "下载"} (1.2s) |
| 谁占用了8080端口 | inspect_system | OK inspect_system {"mode": "port", "target": 8080} (1.1s) | OK inspect_system {"mode": "port", "target": 8080} (1.0s) |
| 把下载里的报表.xlsx移到文档 | file_manage | OK file_manage {"action": "move", "src": "下载\\报表.xlsx", "dst": "文档"} (0.9s) | OK file_manage {"action": "move", "src": "下载\\报表.xlsx", "dst": "文档"} (1.3s) |
| 帮我搜一下今天北京的天气 | web_search | OK web_search {"query": "北京今天天气"} (1.4s) | OK web_search {"query": "北京今天天气"} (0.7s) |
| 现在哪个进程最吃内存 | inspect_system | OK inspect_system {"mode": "top_memory"} (1.1s) | OK inspect_system {"mode": "top_memory"} (0.8s) |
| 给我讲个笑话 | chat | OK chat {} (1.5s) | OK chat {} (1.3s) |
| 截个图 | screenshot | OK screenshot {} (1.2s) | OK screenshot {} (1.2s) |

- 动作命中: A 10/10, B 10/10
- 平均耗时: A 1.2s, B 1.1s
- 提示词长度: A 1811 字, B 1550 字