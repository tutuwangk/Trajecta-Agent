# Agent V3 Flash 部分分布验收（2026-07-31）

> 后续决策（2026-08-01）：用户明确要求不再执行剩余 14 个场景，并授权完成 V3 工程收口后
> 删除 V1/V2。下文保留当时的原始 Gate 判定，作为外部证据限制，而不是当前代码栈说明。

## 结论

本轮使用 `deepseek-v4-flash` 作为 root planner，按单并发执行当前 revision 的
30 条／6 城市／1～5 天影子语料。第 16 个案例首次收到 HTTP 402 后立即停止，未再调用
真实模型：

- 请求 30 条，启动 16 条，14 条保持未启动；
- 13 条形成可评估终态，另有 2 条耗尽 episode 后仍为 `needs_resume`；
- 1 条因余额不足安全暂停为 `needs_resume`；
- 5 条 `succeeded` 并产生 Release，6 条 `waiting_user`，1 条 `failed`，
  1 条 `cancelled`；
- p50 墙钟 370,026 ms，p95 1,130,831 ms；
- `corpus_distribution_gate=false`，`cutover_eligible=false`。

证据目录为
`/tmp/trajecta-v3-shadow-full-30-v20-flash`，attempt 为
`full-30-v20-flash`。`shadow_report.json` 保存机器汇总，
`human_review_queue.json` 保存逐 Release 人工复核。

## 已证明的边界

- 每个 QueryTarget 最多查询一次，候选上下文上限在全部已启动案例中生效；
- HTTP 402 只影响当前案例，后续 14 个案例没有被批量误记为已启动；
- `verified + good + publishable` 之外没有 Release；
- Release 全部绑定本次 run 和 candidate；
- 主动取消案例没有在取消后产生 Candidate 或 Release；
- 歧义和事实缺口会进入 `waiting_user` 或 `needs_resume`，不会降级发布。

## 人工逐日复核

机器 Gate 判定 5 个 Release 均可发布，但人工复核仅 2/5 通过：

| case | 人工结果 | 关键证据 |
|---|---|---|
| `bj-02-two-day-history` | 通过 | 酒店往返、指定午晚餐分店、9 小时上限和交通段均可执行 |
| `cd-01-one-day-explicit-meal` | 不通过 | 时间线把用户名称“成都博舍酒店”显示为 provider 别名“成都居舍”，指定骡马市店显示为“总店” |
| `cd-03-three-day-paced` | 不通过 | 用户明确要求马旺子晚餐，Release 却安排在第 3 天 12:19 |
| `cd-04-four-day-airport-anchor` | 不通过 | 用户没有提供航班时间，Release 自行假定第 1 天 09:00 从机场出发、第 4 天 10:06 到机场 |
| `sh-04-four-day-neighborhood` | 通过 | 四天酒店往返、指定分店、餐饮停靠和交通段完整 |

因此 `coverage_ratio=1` 只证明 obligation 被映射到 stop，不能单独证明：

- 用户地点名称与 provider 别名的展示身份正确；
- 指定午餐／晚餐处于正确餐次；
- 机场锚点基于用户航班时间，而不是模型虚构时间。

## 本轮真实失败及离线修复

`sh-01-one-day-art` 中，Flash 连续提交缺少 `obligation_ids` 的非锚点 stop。
严格领域模型正确拒绝，但转换层的 Pydantic `ValidationError` 没有转成 `ModelRetry`，
最终触发 `UnexpectedModelBehavior` 并把 Run 记为 `failed`。

余额不足后完成了以下离线修复，未重跑真实 provider：

1. 领域转换 `ValidationError` 与 `PlanCommitmentError` 统一变成可修复的
   `ModelRetry`；
2. 当解释模型遗漏约束时，从显式餐厅附近的“早餐／午餐／晚餐”证据确定性补建
   required `TimeWindowRequirement`；
3. 有机场日分配但缺少对应航班时间时，在任何地点搜索前请求用户补充；
4. 时间线展示名由 Requirement Ledger 的用户原始名称拥有，provider 别名和地址保留在
   Grounding 解释中；
5. `--report-existing` 按 `release_id` 保留人工复核，不再覆盖为 pending。

这些修复已有离线回归，但不被倒算为本轮真实 Release 已通过。

## 当时的切流和删除判定

截至该次运行结束时，Gate 判定为不可切流、不可删除 V1/V2，原因如下：

- 30 条只启动 16 条，完整分布 Gate 未通过；
- 人工 Release 通过率仅 40%，存在餐次、机场时间和显示身份问题；
- 有 1 条真实 Run failed、2 条在 3 episode 后仍未收敛；
- 尚无连续 7 天无 P0/P1 证据。

该判定随后被用户的显式产品决策覆盖：未继续测试剩余 14 条，V3 切流与 V1/V2 删除已于
2026-08-01 执行。覆盖 Gate 不会改变本页的失败比例，也不能把未运行样本解释为通过。
