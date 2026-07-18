# ADR-005：Durable Local Runtime 与 Temporal-ready 边界

- 状态：Accepted
- 日期：2026-07-18

## 决策

WP0～WP7 继续使用 FastAPI 与 SQLite，建立 durable local runtime，不在首版引入 Temporal、DBOS 或其他工作流引擎。

项目必须把模型、地图、Web、辅助模型和未来 MCP I/O 隔离为可重放/可幂等 Activity-shaped ports；业务状态、idempotency key、release key、retry 分类和终态语义不依赖具体执行引擎。

## 原因

首要风险是根 Agent 的真实工具行为和 provider message 合同，不是分布式调度。先用真实轨迹确定状态和恢复边界，可避免在 Agent 行为尚未成立前建设错误的事件平台。

## 迁移条件

只有出现多实例竞争、跨进程长任务、需要独立 worker 扩缩容或更严格运行 SLA 时评估 Temporal。迁移时关闭重复的 provider/runtime retry；Temporal 不能替代业务幂等与副作用分类。
