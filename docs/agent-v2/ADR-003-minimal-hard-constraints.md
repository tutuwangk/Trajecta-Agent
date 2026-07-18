# ADR-003：最小硬约束内核与双状态发布

- 状态：Accepted
- 日期：2026-07-18

## 决策

只有系统事实不变量和用户有原文证据且明确表达为不可调整的承诺可以阻止发布。模型推断不得升级为硬约束。

阻断项仅包括：未知/虚构实体、事实所有权违规、日期越界、时间倒序或物理重叠、明确闭馆仍安排、明确不可调整承诺未满足、stale Workspace/fact/candidate version，以及 `spatial_estimate` 被提升为 verified。

强度、舒适度、普通饭点、地点数量、跨区、酒店往返、推荐时段、区域均衡、普通必去、餐饮购物比例均是体验优化目标，通过 `experience_status` 和结构化风险表达，不阻断发布。

## 结果语义

- `fact_status`：`verified/degraded/failed`。
- `experience_status`：`good/needs_adjustment/conflict`。
- `incomplete`：最小发布条件无法闭合，但 Workspace 与诊断有效。
- `failed`：系统或供应商错误且无法安全恢复。

`spatial_estimate` 永远只能参与 degraded 结果。
