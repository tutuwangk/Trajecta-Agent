# WP1 DeepSeek V4 / PydanticAI Provider 合同验收

> 日期：2026-07-18
> 结论：核心 provider Gate 通过；故障注入与业务副作用恢复继续在 WP6 完成
> 版本：`pydantic-ai-slim==2.10.0`，`pydantic-ai-harness==0.7.1`

## 根因与适配结果

PydanticAI 通用 OpenAI Chat adapter 默认可能发送 `tool_choice=auto`，纯工具回合还可能把 assistant `content` 序列化为 `null`。DeepSeek V4 thinking 合同要求不发送 `tool_choice`，工具回合必须保留非空 `content`，并在后续请求回放 `reasoning_content`。

V2 新增 `DeepSeekV4ChatModel`：

- 固定根模型 `deepseek-v4-pro`、轻量模型 `deepseek-v4-flash`。
- thinking 开启，`reasoning_effort=high`。
- 强制省略 `tool_choice`。
- 工具回合 `content=None` 规范为 `""`。
- `reasoning_content` 使用 field 模式保存和回传。
- 不发送 temperature/top_p 等 thinking 无效参数。
- OpenAI SDK 内部 retry 设为 0，后续由 Domain RetryPolicy 单点管理。
- `strict=False`，所有参数由 Pydantic 二次验证。
- final response 只有显式 `finish_reason=stop` 才视为完整；`length/content_filter/None/error` 不得提交候选。

## 自动合同测试

命令：

```bash
backend/.venv/bin/pytest -q backend/tests/contracts
```

结果：`8 passed in 0.57s`。

覆盖：

- thinking 工具回合 `reasoning_content` 回放。
- 不发送 `tool_choice`、sampling 参数。
- assistant tool-call content 非空。
- 无效 JSON/幻觉字段在工具执行前拒绝并反馈修正。
- 未知工具名不执行并形成 retry feedback。
- 同轮两个只读工具结果都闭合后才继续请求。
- `finish_reason=length` 保留并被领域完整性判断拒绝。
- SQLite provider-valid snapshot 重开后可继续，且不存在 orphan tool call。
- Deferred clarification 带原 message history 和相同 tool-call ID 继续。

## 真实 V4 Pro canary

命令：

```bash
backend/.venv/bin/python backend/scripts/run_deepseek_v4_contracts.py --runs 30
```

正式稳定性样本结果：`30/30 passed`，未处理协议错误 `0`。每次均为 3 个 provider request、2 个有依赖工具调用，严格按 `get_contract_date → lookup_contract_fact` 完成并输出 `CONTRACT_OK`；单次耗时约 4.66～8.95 秒。

随后把 canary 加深为三轮有依赖工具：

```text
get_contract_date
→ lookup_contract_fact
→ submit_contract_candidate
→ final CONTRACT_OK
```

smoke 结果：`1/1 passed`，4 个 provider request、3 个 tool call、9.3 秒。

canary 只输出工具序列、请求数、耗时和错误分类，不输出 chain-of-thought 或敏感配置。

## Harness 真实边界

`StepPersistence` 的 SQLite store 能保存 provider-valid snapshot 并跨 store 实例继续。但 Deferred Tool 在第一段 provider run 中仍保留 unresolved tool-effect；第二段带 `DeferredToolResults` 的 provider run 不会自动回写闭合第一段 ledger。

因此：

- 一个业务 `AgentRun` 可以对应多个 provider run。
- `conversation_id` 用于对话分组，provider `run_id` 每次 `.run()` 唯一。
- 用户答案唯一消费、deferred effect reconciliation、Workspace mutation 与 Release 幂等必须由 Trajecta Domain Harness 管理。
- Harness types 不进入领域 API。

## 尚未由 WP1 证明

- 真实 429/5xx/网络超时故障注入及 retry 预算。
- 真实进程在 tool started/completed 中间终止后的业务 mutation 恢复。
- 真实超长网页/地图工具结果的外置与再读取。
- 30 次三工具深度 canary；当前 30 次稳定性样本为两工具，另有 1 次三工具 smoke。

这些事项不改变 provider 协议 Gate 结论，但必须在 WP3/WP6 的真实工具和故障注入中完成，不能用本报告宣称整个 durable runtime 已完成。
