# 贾维斯 · Windows 桌面语音助手

一个跑在本机的 Windows 桌面助手：喊一声「贾维斯」就能打开软件、查资料、调音量、截图锁屏，也能看屏幕、闲聊，并把复杂目标拆成多步任务去执行；支持语音唤醒、全局热键和纯打字三种输入方式，界面可选 Web 胶囊、全屏 HUD 和经典窗口。

## 功能亮点

- **三种界面，随手切换**：默认是 Web 胶囊；托盘菜单可以展开全屏 HUD（经典大中枢）、切到悬浮球等界面，悬浮球双击/右键也能进入全屏。全屏 HUD 左栏有四个「战术动作插槽」：快速截图、立即锁屏、查询天气、启动记事本。
- **多种唤醒方式**：语音唤醒词默认是「贾维斯」和「星光」，热键默认是 `Ctrl+Alt+J`；两者都能在配置里改。麦克风不方便时可用纯文字模式。
- **大脑可以换**：支持 DeepSeek、通义千问、智谱、Kimi、Google Gemini、OpenAI、Anthropic Claude、任意 OpenAI 兼容接口，以及 Kaggle 免费 GPU 上的 `qwen3.6-35b-a3b`。没配大模型时，本地规则仍能执行常用指令；看屏用的视觉模型可单独指定，DeepSeek 下默认走 `deepseek-flash`。
- **多步任务执行**：复杂目标先出计划、分步执行、每步回读核实，结果按「成功 / 失败 / 未核实」如实汇报；某步失败时最多让模型修一次方案再重试。关机、重启、关闭软件等危险操作必须二次确认。
- **不只是执行指令**：闲聊走独立的对话通道，带上下文和本地记忆，可以聊日常、问问题，而不是只会匹配关键词。
- **联网与天气**：搜索会在 Bing、360、搜狗、DuckDuckGo 之间轮换，回答末尾带来源；天气走专用数据源（Nominatim + Open-Meteo，备链 wttr.in），支持全国城市的今天/明天/后天预报。
- **看屏（peek_screen）**：问一句「看看屏幕上这个报错是什么意思」，它会截屏并让视觉模型作答。截图只在内存里处理、不落到磁盘，但**会发送给你自己配置的视觉模型服务商**，请留意这一点。
- **本地记忆**：记住主人的偏好、事实和被纠正过的说法；带 RAG 检索，仓库内含语义向量检索实现，缺少向量模型文件时自动回退到字面匹配。`training/seed_training.py` 可写入种子记忆。
- **语音识别防护**：重复解码幻觉会被拦截，底噪不送识别，低置信度结果只提示、不执行动作。
- **系统工具（白名单）**：打开/关闭软件（智能查找启动路径）、音量、亮度、锁屏、截图（保存目录可改）、文件管理（只限桌面/下载/文档，不能删除）、打开文件/网址、写文件、只读系统检查等。

## 快速开始

### 环境

- Windows 系统；程序使用 Windows 语音、托盘和桌面相关能力。
- Python 3.11 或 3.12（64 位）。安装时勾选 **Add python.exe to PATH**。
- Web 胶囊和全屏 HUD 基于 pywebview；如果提示缺少 WebView2 运行时，需要先安装微软 WebView2 运行时。

### 源码运行

1. 把仓库放到一个固定目录（例如 `D:\Jarvis`），不要放在临时目录。
2. 双击 **`install.bat`**：它会创建 `.venv` 并安装 `requirements.txt`。
3. 双击 **`Jarvis.vbs`**：无黑窗口启动图形界面，默认显示 Web 胶囊；右下角托盘图标可以切换界面或退出。
4. 也可以按需使用终端入口：
   - **`start.bat`**：在终端里运行，方便看报错（`python jarvis.py`）。
   - **`start-text.bat`**：纯打字模式（`python jarvis.py --text`），麦克风不可用时用它。
   - 其他参数：`python jarvis.py --once "截图"` 只执行一条指令；`--dry-run` 只解析不执行。
5. 如果麦克风被 Windows 拦住：设置 → 隐私和安全性 → 麦克风，打开「麦克风访问」和「允许桌面应用访问」。

### 界面切换

- 托盘菜单：「显示 Web 胶囊」「展开全屏大中枢」「切换为桌面悬浮球」「隐藏界面」等。
- 悬浮球：双击进入全屏 HUD，右键菜单也可切换界面或退出。
- 也可以用 `python jarvis_gui.py --ui web` 指定启动的界面风格（可选 `new`、`classic`），默认是 `web`。

### 自行打包

仓库提供 `Jarvis.spec`（PyInstaller）和 `installer.iss`（Inno Setup）打包配置；打包脚本会主动剔除 `config.json` 里的 `api_key`，不会把个人密钥打进安装包。仓库里没有附带预编译好的安装包。

## 配置大脑与视觉模型

推荐直接在界面的配置面板里填，也可以编辑配置文件。程序先读仓库目录下的 `config.json`，再用 `%APPDATA%\Jarvis\config.json` 里的同名项覆盖；界面保存的设置写入后者，重装或重新打包不会被冲掉。

| 配置项 | 作用 |
|---|---|
| `ai_provider` | 服务商。代码默认是 `DeepSeek`；配置模板示例是 `Kaggle 免费 35B`。 |
| `api_key` | 服务商密钥，也可以放在环境变量 `AI_API_KEY` 里。Kaggle 免费 35B 不需要密钥。 |
| `ai_model` | 模型名。自定义 API 必须填；Kaggle 示例是 `qwen3.6-35b-a3b`。留空时用所选服务商的预设模型。 |
| `ai_url` | 接口地址。OpenAI 兼容接口只填域名或填到 `/v1` 都可以，程序会自动补成 `.../v1/chat/completions`。Kaggle 要填 Notebook 重跑后给出的 trycloudflare 域名（地址会变，变了就改这里）。 |
| `vision_model` | 看屏模型。留空时 DeepSeek 下用 `deepseek-flash`，其他服务商沿用 `ai_model`；视觉请求复用同一份 `api_key` / `ai_url`，没有单独的视觉密钥。 |

不配大模型也能用：打开软件、音量、亮度、截图、锁屏、改截图目录等本地规则指令照常工作，搜索会退回为在浏览器打开搜索页；遇到不认识的指令会如实说没学会。自由对话、带出处的联网回答、多步规划和看屏需要配置模型才能用。

仓库里的 `.env.example` 和 `gemini_model` 是历史遗留内容，按上表配置即可，不需要照旧说法去填 Gemini 密钥。

## 指令示例

| 说法示例 | 效果 |
|---|---|
| 打开记事本 / 打开微信 / 打开 steam | 在已映射或已安装的软件里查找并启动 |
| 关闭微信 | 关闭软件，执行前需要确认 |
| 打开B站 / 打开知乎 | 打开常用网站 |
| 搜索周杰伦 / 百度一下天气 | 联网搜索，回答带来源 |
| 明天合肥天气怎么样 | 查询指定城市今天/明天/后天的天气 |
| 打开桌面 / 打开下载文件夹 | 打开授权目录下的文件夹 |
| 打开《某个文件名》 | 在桌面、文档、下载里找文件并打开 |
| 音量调大 / 静音 / 音量调到50 | 调整系统音量 |
| 亮度调到70 | 调整屏幕亮度（部分显示器可能不支持） |
| 截图 / 以后截图放到工作区 | 截图，或修改截图保存目录（默认在「图片\\贾维斯截图」） |
| 锁屏 | 锁定电脑 |
| 关机 / 重启 / 取消关机 | 定时关机或重启，执行前需要二次确认；可取消 |
| 看看屏幕上这个报错是什么意思 | 截屏并让视觉模型解释（截图会发给视觉模型服务商） |
| 你觉得这件事该怎么办 / 讲个笑话 | 走自由对话通道，结合上下文和记忆回答 |

## 安全与隐私

- 只执行白名单里的动作；模型返回白名单之外的动作会被丢弃或降级为对话。
- 关机、重启、关闭软件、移动文件等危险步需要你确认；文件管理只限桌面、下载、文档，不支持删除。
- 密钥只保存在本机配置里（界面保存时写入 `%APPDATA%\Jarvis\config.json`）。不要把密钥发到聊天里，也不要把填了密钥的 `config.json` 提交进仓库。
- 看屏截图只在内存里处理、不写文件，但会作为图片请求发送给你配置的视觉模型服务商；是否使用取决于你对这家服务商的信任。
- 联网搜索和天气会向相应服务发出查询（天气查询会带地理位置信息）。

## 自定义软件

打开 `config.json`，在 `apps` 里按「名称: exe 名」加一行，例如：

```json
"剪映": "JianyingPro.exe"
```

保存后说「打开剪映」即可。exe 名可以在任务管理器里右键该软件 → 打开文件所在位置看到。用户配置会与内置映射合并，不会丢掉原有条目。

## 项目结构

| 文件 | 作用 |
|---|---|
| `jarvis.py` | 主循环、本地规则、动作执行、看屏与配置读写 |
| `jarvis_gui.py` | 图形界面、悬浮球、系统托盘、界面切换与全屏 HUD |
| `web_capsule.py`、`web_ui/`、`web_fullscreen.py`、`web_fullscreen_ui/` | Web 胶囊与全屏 HUD 的前端和桥接 |
| `llm_brain.py` | 服务商预设、规划/聊天/视觉请求、动作白名单校验 |
| `agent_core.py` | 与平台无关的多步任务状态机（含失败修复一次） |
| `system_tools.py` | 文件管理、系统检查、只读 PowerShell 的安全边界 |
| `web_tools.py`、`weather_tools.py` | 联网搜索与专用天气数据源 |
| `whisper_engine.py` 等语音模块 | 本地识别、底噪/幻觉防护、语音合成 |
| `jarvis_memory.py`、`rag_engine.py` | 本地记忆与检索 |
| `training/` | 种子记忆脚本、考试题与回归测试 |
| `config.example.json`、`.env.example` | 配置示例（后者为历史遗留） |
| `install.bat`、`start.bat`、`start-text.bat`、`Jarvis.vbs`、`Jarvis.spec`、`installer.iss` | 安装、启动与打包 |

## 测试

`training/` 里是可直接运行的自建脚本（看退出码，不依赖 pytest）。在仓库目录、已激活 `.venv` 的环境下运行：

```bat
python training/seed_training.py
python training/run_exam.py --url 你的模型地址
python training/run_exam_agent.py --url 你的模型地址
python training/run_exam_state.py
python training/run_exam_exec.py --url 你的模型地址
python training/run_exam_web.py --url 你的模型地址
python training/test_repair.py
python training/test_chat_channel.py
python training/test_route_arbitration.py
python training/test_asr_guard.py
python training/test_peek_screen.py
python training/test_screenshot_dir.py
python training/test_orphan_sweep.py
python test_weather.py
```

`seed_training.py` 写入种子记忆；`run_exam_state.py` 是状态机考试，不需要模型；`run_exam_exec.py` 会造临时文件；`run_exam.py`、`run_exam_agent.py`、`run_exam_web.py` 的 `--url` 也可用环境变量 `JARVIS_EXAM_URL` 提供（`run_exam_exec.py` 的 `--url` 必填）；其余 `test_*.py` 是不需要模型的回归测试。详细说明见 `training/README.md`。

## 注意事项

- 唤醒靠短句语音识别轮询，不是离线唤醒芯片；安静环境效果更好，嘈杂环境建议用热键或文字模式。
- 语音识别默认优先本地 Whisper，也可使用系统语音引擎或 Vosk；Whisper 模型较大时会占更多内存和 CPU。
- Kaggle 免费 35B 的 trycloudflare 地址每次重跑都会变，改 `ai_url` 即可；额度用尽或会话停止时，大脑不可用，本地规则仍可用。
- 视觉模型要支持图片输入；看屏失败时优先检查 `vision_model` 和所选服务商是否支持视觉请求。
- 仓库中的配置示例值（例如 Kaggle 的模型名、DeepSeek 的视觉模型名）只是示例，具体是否可用以你自己的服务商账号为准。
