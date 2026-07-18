# WP2 V2 领域与持久化骨架验收

> 日期：2026-07-18
> 结论：通过，可以进入 WP3 纵向闭环

## 已完成

- 建立独立 `backend/app/trip_agent/` 包。
- 建立严格、冻结、拒绝额外字段的 V2 领域模型：Goal、Claim、Place、Draft、Candidate、AgentRun、Clarification、Release、TripWorkspace。
- Claim 明确分为 Observed、Derived、Estimate、Decision、UserCommitment；Observed 必须有来源，`spatial_estimate` 不能成为 release-eligible verified fact。
- Run 状态为 `created/running/waiting_user/validating/published/incomplete/failed/cancelled`，所有终态不可覆盖。
- Workspace 使用单调版本；Draft 必须引用当前版本，place/visit/day 结构保持唯一和连续。
- 澄清批次限制 1～5 题，答案按 interruption/tool-call ID 唯一消费。
- 新建 V2-owned SQLite 仓储与五类表：Workspace、snapshot、run、interruption、release；不复用 Legacy session/planning schema。
- Workspace compare-and-swap、run idempotency、answer once、release key 唯一和跨 repository reopen 已实现。
- `AgentPersistencePort` 隔离 Harness 类型；业务 run ID 与 provider run/conversation 身份分开。

## 架构隔离

AST import test禁止 `trip_agent/` 导入：Legacy planning workflow、planner、intent ledger、POI extractor、schedule evaluator、itinerary normalizer、POI grounder 和 Legacy orchestrator。

允许的复用必须通过 V2 adapter，当前 WP2 尚未导入任何 Legacy 规划决策。

## 验证

```bash
backend/.venv/bin/pytest -q backend/tests/trip_agent
```

结果：`13 passed in 0.07s`。

其中领域/架构 8 个测试，SQLite repository 5 个测试。另有 WP1 provider contracts 8 个测试独立通过。

## WP3 入口

下一步只能在这些合同之上建立根 Agent 和最小工具集。fixture 纵向闭环必须从原始输入创建 Workspace/Run，不得调用 Legacy `/recognize-places`、planner、normalizer 或 deterministic fallback。
