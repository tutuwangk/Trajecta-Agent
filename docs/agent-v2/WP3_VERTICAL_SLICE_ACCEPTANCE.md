# WP3 AI-Native 纵向闭环验收

日期：2026-07-18

## 结论

WP3 的核心纵向闭环已建立：原始旅行需求直接创建 `TripWorkspace` 和有限生命周期的
`AgentRun`，唯一根 Agent 自主选择工具，使用已搜索的 candidate ID 形成草案，经确定性
模拟后提交不可变候选并发布。V2 没有导入 Legacy planner、workflow、POI score、normalizer
或 deterministic itinerary fallback。

## 已实现合同

- `read_workspace`
- `analyze_place_mentions`
- `search_place_candidates`
- `resolve_place`
- `apply_draft_change`
- `simulate_candidate`
- `request_clarification`
- `submit_candidate`
- 运行未提交候选时进入 `incomplete`，不由代码替 Agent 规划
- 澄清以 `tool_call_id` 持久化，原 `run_id` 暂停并恢复
- Deferred Tool 的 provider transcript 与领域 Workspace 隔离存储

## 实际验证

```text
backend/.venv/bin/pytest -q backend/tests/trip_agent backend/tests/contracts
24 passed
```

真实 DeepSeek V4 Pro 纵向 canary：

```text
backend/.venv/bin/python backend/scripts/run_trip_agent_v2_vertical_canary.py
status=published
elapsed_seconds=22.12
events=run_started -> place_mentions_analyzed -> place_candidates_found ->
       place_resolved -> draft_changed -> candidate_simulated -> candidate_published
deterministic_fallback=false
```

该 canary 使用受控地点 fixture，目的仅是隔离验证真实根模型的多轮工具决策。真实高德候选、
网页事实、路线事实和体验验收属于 WP4-WP5，不能把本结果解释为生产路线质量已经达标。

## 发现并修复的协议缺口

`pydantic-ai-harness` 的 `StepPersistence` 只保存 provider-valid 边界；Deferred Tool 包含尚未
完成的工具调用，因此不会产生 continuable snapshot。V2 为此增加独立 opaque transcript
存储，只保存恢复协议需要的消息。它不进入 `TripWorkspace`、不成为业务事实，也不向用户
展示 reasoning。

## 后续 Gate 完成状态

WP4-WP7 已在该闭环之上接入真实高德候选、来源级 Claim、批量路线事实、完整时间线编译、
最小 `ReleaseGate`、durable runtime 和 V2 浏览器入口。当前最新真实链路证据与剩余 WP8
统计边界统一记录在 `WP4_WP7_IMPLEMENTATION_STATUS_2026-07-18.md`；本文件保留 WP3
当时的 fixture canary，避免用后来的能力改写早期 Gate 证据。
