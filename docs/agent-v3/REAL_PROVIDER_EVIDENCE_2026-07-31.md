# Agent V3 真实 Provider 证据（2026-07-31）

## 最新 Flash 分布

attempt `full-30-v20-flash` 显式使用 `deepseek-v4-flash` root planner。
30 条中启动 16 条后，`hz-01-one-day-west-lake` 收到 HTTP 402；运行器立即停止，
其余 14 条未启动。13 条可评估终态中有 5 条发布、6 条等待用户、1 条失败、1 条取消，
另有 2 条耗尽 episode 后保持 `needs_resume`。

5 个 Release 的机器 Gate 全部通过，但人工逐日复核只有 2/5 通过。失败包括指定晚餐被排在
12:19、缺少航班时间却发布机场时间线，以及 provider 别名覆盖用户地点名。完整证据和离线
修复见
[`PARTIAL_DISTRIBUTION_2026-07-31.md`](./PARTIAL_DISTRIBUTION_2026-07-31.md)。

## 证据边界

本记录只保存 run-bound、安全聚合结果，不保存 API key、provider transcript、模型 reasoning、
原始网页正文或本地 SQLite。它不能替代 30-run 分布、人工逐日复核和连续 7 天稳定性。

## 成都显式餐饮场景

业务运行：`run-a89f2f1e-a3fc-403a-b7b3-de43fb000dc4`

- 资料中 4 个显式地点编译为 4 个 obligation 和 4 个 QueryTarget；
- 高德返回 29 个原始候选，Runtime 只保留 10 个，另有 7 个因每目标上限被截断；
- 4 个 QueryTarget 各查询一次，4 个地点均完成 selected grounding；
- 草案有 5 个可见 stop，指定餐厅是实际 meal stop；
- 事实范围仅含计划内 4 个 route need 与 3 个 operational need；
- 单次局部上下文峰值 3,227 字符；
- 没有产生 Release。

机器检查结果：

| Gate | 结果 |
|---|---|
| explicit place coverage | pass |
| named meal complete | pass |
| candidate context cap | pass |
| one search per QueryTarget | pass |
| scheduled-only fact scope | pass |
| complete timeline | pass |
| fact gaps explained | pass |
| operational fact contract | pass |
| strict publication | pass |
| release lineage | pass |
| blocker specificity | pass |
| cancellation artifact safety | not exercised |
| human review and 7-day stability | not exercised |

该运行先被具体运营事实缺口和真实时间窗冲突阻断；Agent 修订后仍未通过事实门禁。随后
DeepSeek 返回 HTTP 402。因为这是错误分类修复前启动的进程，它的终态被旧代码记为 `failed`；
该状态不作为当前状态机验收证据。

## 当前状态机真实重放

业务运行：`run-f1307e78-43e1-4e94-a76e-1fd7aeb081f2`

在状态语义修复后，同一真实 DeepSeek HTTP 402 被记录为：

- `run_status=needs_resume`；
- 事件 `provider_blocked`；
- `reason_code=provider_balance_insufficient`；
- `release_id=null`；
- `cutover_eligible=false`。

这证明外部账户阻断不会再伪装成业务完成、不可变发布或不可恢复的系统失败。账户恢复后应通过
`POST /api/v3/agent-runs/{run_id}/resume` 复用同一 run 与检查点继续。

完成审计后的最新探针 `run-8ae6687a-bf1f-469b-ace0-9d66506bd115` 再次得到相同 402、
`needs_resume` 和 `release_id=null`。影子 runner 已确认对
`retryable_now=false` 只执行一次，不在同一 invocation 内自动消耗重复请求。

## 尚缺证据

- 当前代码版本的 30 条、6 城市、1～5 天真实分布；
- 真实取消后的 Candidate／Release 安全样本；
- 多轮人工地点确认恢复样本；
- 人工逐日可执行性复核；
- 连续 7 天无 P0/P1。

在以上证据完成前，根路径保持 V2，V3 只作为 `/v3` 影子入口。
