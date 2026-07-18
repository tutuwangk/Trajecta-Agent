# ADR-002：TripWorkspace、AgentRun 与 Release

- 状态：Accepted
- 日期：2026-07-18

## 决策

- `TripWorkspace` 是单次旅行的长期协作状态，保存目标账本、地点假设、来源与 claim、草案、开放问题和发布历史。
- `AgentRun` 是一次初始规划或修改任务，状态为 `created/running/waiting_user/validating/published/incomplete/failed/cancelled`。
- `Release` 是不可变发布版本。
- Agent 主动澄清继续同一业务 run；Provider 可以产生新的底层 `.run()`，但必须通过 conversation/lineage 映射回原业务 run。
- 发布后的新修改创建 revision run，读取同一 Workspace 和上一版 Release。
- 所有终态不可覆盖；Workspace 使用单调版本，Repository mutation 必须进行当前版本 CAS。版本令牌
  由 Runtime 在执行时提供，不进入模型工具 schema。

## 边界

运行期间不接受用户主动追加要求，只允许回答 Agent 发起的 1～5 个问题。第一阶段不自动形成跨旅行用户记忆。Provider transcript 与 Workspace 分库存储，不能进入用户 API。
