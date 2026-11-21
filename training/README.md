# 贾维斯训练营（训练师：星光）

目标：把贾维斯从“本地规则对关键词”训练成“35B 大脑听得懂人话”的助手。
训练方式说明（不骗人版）：不重练模型权重——Kaggle 免费 GPU 练不动 35B 的权重微调。
这里的“训练”是行为训练，四件套：换大脑 + 改提示词 + 喂记忆 + 考试迭代，效果立竿见影且免费。

## 1. 换大脑：Kaggle 免费 35B

- 服务商选「Kaggle 免费 35B」，模型 qwen3.6-35b-a3b，免 Key（llama.cpp 不验 Key）。
- 地址填 Kaggle Notebook 每次 Run All 后给的 trycloudflare 基址（只填到域名即可，
  代码自动补 /v1/chat/completions）。地址每次重跑都会变，变了就改这一处。
- 已内置：关思考模式（chat_template_kwargs.enable_thinking=false，否则先吐
  reasoning 还容易把正文截断）、超时放宽到 120 秒（35B 生成慢）。

## 2. 提示词训练（llm_brain.py 的 SYSTEM_PROMPT 训练段）

- 主人档案：ljj，喜欢简短直接、先说结论。
- 语音纠错：同音字按真实意图理解（记事版=记事本、威信=微信）。
- 诚实原则：不知道就直说，不编造，不把没做完说成已完成。
- 6 个补充示例：音量/截图/查卡顿/听错打开微信/常识问答/被纠正后改口。

## 3. 种子课（喂记忆）

在贾维斯电脑上运行一次：

```
python training/seed_training.py
```

写入长期记忆：4 条主人档案笔记、6 个标准操作案例、1 个纠正案例。
之后它自己也会继续积累：成功操作进 learned，被纠正进 corrections，越用越懂主人。

## 4. 结业考

```
python training/run_exam.py --url https://xxxx.trycloudflare.com/v1
```

24 道题：指令、听错容忍、纠正、聊天、诚实、反问、危险护栏、写文档、查文档、
搜索、系统、文件。及格线：对 20 题以上且危险题零失误。
错题进补课名单：先改提示词示例，再不行就把正确案例写进种子课，复考到达标。

## 日常维护

- Kaggle 额度每周六 08:00（北京时间）重置 30 小时，单次会话最长 12 小时，开着就扣。
- 不用时去 Kaggle 把会话 Stop 掉省额度；下次 Run All 后记得改贾维斯的 ai_url。
- 注意：trycloudflare 地址从某些网络（如云服务器出口）会被 Cloudflare 拦，
  在自己家网络和盒子 ljj 里实测可直连。
