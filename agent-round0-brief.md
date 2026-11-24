# 第 0 轮：地基修复（分支 agent-foundation）——只修不新增能力

背景：智能体化改造前，Codex+OpenCode 双审在现有 agent 链上查出一批
一致性与安全欠账。本轮全部修掉并补回归测试，不新增任何工具/动作。

1. **成功位诚实**：审计 _perform_plan 全部动作的成功位来源。
   已知：peek_screen 无条件 action_ok=True——被敏感闸门跳过、视觉失败时
   也报成功，诊断永远不触发；open_url 协议不合法仍 True。
   规则：每个动作的成功位必须来自真实结果（跳过/未核验/失败=False 或
   明确的未核验状态），不许默认 True 兜底。逐个动作过一遍并在报告里列清单。
2. **move 护栏统一**：file_manage:move 在直连路径 danger=True 要确认，
   在 agent 路径却是 notify 不确认。统一到单一作用域来源：
   agent 路径的 move 与直连同级（ask/确认），两条路结论一致，补对照测试。
3. **guard_desc 不许丢**：sanitize_plan 给 move/run_powershell 等生成的
   guard_desc 在 agent_draft_plan 归一时被丢掉，agent_continue 只报 step.say，
   用户不知道在放行什么。归一保留 guard_desc，继续/放行时确认话术必须
   含 guard_desc（具体命令/路径）。
4. **修复后权限重算**：agent_continue 现在把 ASK 步降 NOTIFY 并置
   external_ok；若修复步换了 action/args 仍用旧放行。改：放行前按当前
   step 的 action/args 重算 scope，级别升高则重新挂起问用户。
5. **来源绑定**：_agent_perform 调 _perform_plan 不传 source，危险确认
   按 local 处理；_agent_control_command 不校验来源，遥控「继续任务」
   能放行本地任务的待确认危险步。改：任务记来源，执行与继续都带来源，
   待确认步只认同源放行（与既有确认来源隔离同一套规则），补跨源测试。
6. **账本原子写+脱敏**：_agent_save_tasks 整文件覆盖、无锁、存完整
   write_file.content 与命令原文。改：临时文件+原子替换、加写锁、
   敏感字段只存摘要（内容存长度+哈希或截断预览，命令脱敏），
   读取端兼容旧格式。
7. 不许动 agent_core 的状态机硬约束（不调大模型、不碰平台）；
   _agent_execute_step/_agent_loop 是旧测试壳，生产走 _RUNNER，别在壳上加功能。

测试：每条一个回归用例（training/test_agent_foundation.py），
既有全部测试不回归（test_export_zip、test_orphan_sweep 两环境失败除外），
语料/反例/指代考卷保持全过。交付：分支提交，最终消息逐项报告
（成功位审计清单附上：每个动作改前/改后）。
