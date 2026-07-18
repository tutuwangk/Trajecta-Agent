# ADR-004：DeepSeek V4 与 PydanticAI/Harness

- 状态：Accepted with WP1 gate
- 日期：2026-07-18

## 决策

目标生产配置为 DeepSeek V4 Pro 根 Agent、V4 Flash 轻量结构化工具。PydanticAI Core 负责 Agent loop、Toolsets、消息类型和 Deferred Tools；`pydantic-ai-harness` 的 `StepPersistence` 是 provider step/transcript 持久化候选，通过项目 `AgentPersistencePort` 隔离。

当前仓库只有 `pydantic-ai-slim[openai,retries]==2.10.0`，尚未安装 Harness，且生产 adapter 默认 V4 Flash、关闭 thinking。因此本 ADR 不把具体版本组合标为已验证；WP1 的 30 次真实 canary 是采用前硬 Gate。

## Provider 合同

- thinking 工具回合保存并完整回传 `reasoning_content`。
- thinking 请求不发送 `tool_choice`，assistant tool-call message 保留非空 `content`。
- 不发送 thinking 模式无效的 sampling 参数。
- 工具参数必须经 Pydantic 二次验证；首阶段不依赖 Beta strict mode。
- reasoning 仅作为 opaque runtime transcript，不进入事实、审计结论或用户界面。

## Harness 边界

`StepPersistence` 提供 append-only step event、provider-valid snapshot 和 tool-effect ledger，但不是 Workspace/graph checkpoint，也不提供业务 exactly-once。mutation、答案消费、发布幂等和 unknown-after-crash 恢复由 Trajecta Domain Harness 负责。Harness 处于 0.x，必须精确锁版本并在升级前重跑合同矩阵。

## 资料

- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [DeepSeek Agent Integration](https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi)
- [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [Pydantic AI Step Persistence](https://pydantic.dev/docs/ai/harness/step-persistence/)
- [Pydantic AI Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
