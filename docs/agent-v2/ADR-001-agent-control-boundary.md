# ADR-001：根 Agent 与确定性系统的控制边界

- 状态：Accepted
- 日期：2026-07-18

## 决策

生产核心只保留一个 `PlannerAgent`。它拥有旅行方案中的语义取舍：地点取舍、分天、排序、餐饮策略、草案修复、继续查证、主动澄清和候选提交。

确定性系统拥有：外部事实获取、真实候选 ID、实体层级、坐标、地址、路线、日期与分钟算术、原子 mutation、版本、幂等、预算、恢复和最小 ReleaseGate。

V4 Flash 只作为无长期记忆、无 Workspace 写权限的结构化工具，不是第二个领域 Agent。V2 不使用固定业务阶段图、多 Agent、Subagents、Code Mode 或 deterministic itinerary fallback。

## 原因

自主性必须存在于需要整体权衡的决策处，而不是让模型接管可确定计算或让 Python 在模型失败后暗中替它规划。这样既能产生状态相关的工具轨迹，也能保证事实所有权、恢复和发布安全。

## 强制验证

- V2 import graph 不得依赖 Legacy planner/workflow/normalizer/grounder 决策。
- 不同场景的工具序列不能是固定模板。
- 所有发布地点引用真实候选 ID。
- deterministic itinerary fallback 恒为 0。
