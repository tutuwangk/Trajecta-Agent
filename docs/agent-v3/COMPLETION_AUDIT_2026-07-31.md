# Agent V3 完成审计（2026-07-31）

> 2026-08-01 状态更新：用户明确终止剩余 14 个真实场景，并授权完成工程收口后删除 V1/V2。
> V3 已切为根单栈，旧代码已删除；下文的 16/30 和人工 2/5 仍是历史外部证据限制，不代表通过。

> 最新真实分布结论：Flash 本轮启动 16/30 后因余额不足停止，5 个 Release
> 人工仅 2 个通过；`cutover_eligible=false`。详见
> [`PARTIAL_DISTRIBUTION_2026-07-31.md`](./PARTIAL_DISTRIBUTION_2026-07-31.md)。

## 判定规则

“代码存在”不等于完成。本审计将证据分成四层：

1. 领域不变量：错误状态在模型层无法构造；
2. 自动旅程：真实调用链或端到端 fixture 证明行为；
3. 用户交付面：当前 run 的处置、时间线、事实与状态可见；
4. 生产证据：当前 provider 分布、人工执行性与连续稳定性。

前 3 层完成只能证明实现可进入影子验收；第 4 层缺失时禁止切流。

## 原始问题逐项审计

| 原始问题 | 当前责任边界 | 自动／实现证据 | 真实证据 | 判定 |
|---|---|---|---|---|
| 原始资料 POI 提炼失控 | 精确 span Requirement Ledger；显式地点不得伪装成 reference；QueryTarget 白名单 | `test_requirement_compilation.py`、`test_deepseek_adapters.py` | 成都真实运行 4 个显式地点形成 4 个 QueryTarget | 影子实现完成 |
| POI 筛选缺失 | 时间、行动、节奏、交通进入 constraint；只允许 query/merge/clarify 的地点查询 | `test_query_plan.py`、`test_requirement_compilation.py` | 同上 | 影子实现完成 |
| 候选消歧不可用 | 每个原始 target 独立 CandidateSet、选择理由、置信度、备选与澄清 | `test_grounding.py`、`DeliveryPanel.tsx` | 4 个 target 均完成 selected grounding | 影子实现完成 |
| 餐饮需求丢失 | named meal obligation 必须映射到真实 meal stop，显式餐次必须形成时间窗 | `test_plan_graph.py`、`test_plan_commitment.py`、meal-slot inference test | 真实 Release 曾把指定晚餐排在 12:19；已离线修复，待真实重验 | 生产证据未通过 |
| 显式 POI 覆盖缺失 | 每项必须 scheduled/pending/not scheduled/excluded，省略需原因 | `test_requirements.py`、Coverage UI | 单场 coverage ratio 1 | 影子实现完成 |
| 酒店锚点不可见 | 每天首尾必须是 lodging/airport；缺酒店或机场航班时间在搜索前 waiting_user | anchor gap tests | 真实机场 Release 曾虚构出发／抵达时间；已离线修复，待真实重验 | 生产证据未通过 |
| 草案不是完整时间线 | Python 编译 Stop/Leg 出发、到达、方式、时长、停留 | `test_timeline_compiler.py`、timeline view test | 单场 timeline complete Gate 通过 | 影子实现完成 |
| 事实查询资源浪费 | 路线和运营 FactNeed 只能从已提交草案派生；失败缓存按身份复用 | `test_fact_needs.py`、Runtime cache tests | 只查询 4 route + 3 planned operational needs | 影子实现完成 |
| 外部事实失败不可解释 | FactGap 带天数、地点、仍在行程、影响和建议 | `test_fact_gap_report.py`、Runtime failure tests、FactGap UI | 单场具体 blocker Gate 通过 | 影子实现完成 |
| Agent 上下文污染 | 局部分页上下文；业务 checkpoint 后清空 provider transcript；Grounding 单向锁 | `test_local_context.py`、checkpoint compaction test | 上下文峰值 3,227 字符；30-run 分布待跑 | 影子实现完成 |
| Agent 自主性无目标闭环 | 单根 Agent 决策；Runtime 拥有不变量；非法方案 ModelRetry；纯事实 blocker 转确认 | goal loop、runtime、deepseek adapter tests | 单场完成时间窗修订，后被事实门禁阻断 | 影子实现完成 |
| 运行／交付状态脱节 | RunStatus、DeliveryState 分离；Resume API；provider_blocked；取消不再同时 failed | API 与 repository state tests、中文状态 view test | 真实 402 为 needs_resume 且无 Release | 影子实现完成 |
| 当前运行与 Release 关联错误 | Candidate/Assessment/Release 全部绑定 producing_run_id；V2 当前 run 读取修复 | `test_delivery_routes.py`、V2 前端修复 | 单场 lineage Gate 通过 | 影子实现完成 |
| 发布资格过宽 | verified + good + 全覆盖 + 无 blocker 才可发布 | `test_delivery_gate.py`、成都严格发布 E2E | 真实事实缺口未发布 | 影子实现完成 |
| 发布文案误导 | 只有严格 Release 显示“已核验发布方案”；技术快照明确待复核 | frontend view tests、V2 Release card | 真实 402 release_id=null | 影子实现完成 |
| 风险提示泛化重复 | ExperienceIssue 必须带 obligation、地点或天数；重复汇总事实风险被抑制 | delivery/experience tests | 单场 blocker specificity Gate 通过 | 影子实现完成 |
| 验收范围失真 | 33 项可执行风险 manifest、30 条真实语料、13 项硬 Gate、人工复核队列 | manifest/corpus/shadow aggregate tests | 启动 16/30；机器发布 5 条，人工仅 2/5 通过 | 生产证据未通过 |
| 产品目标偏离 | V3 完成条件是可执行旅行交付，不以 checkpoint、恢复或工具次数代替 | `ARCHITECTURE.md`、严格 ReleaseGate | 单场因事实缺口拒绝发布 | 影子实现完成 |

## 额外审计发现

- 显式可排程地点曾可被模型标成 `reference`：现已在 DTO、proposal、obligation 三层禁止。
- 不可发布后曾重新打开 Grounding：现已加入单向阶段锁。
- 非法 plan commitment 曾使 run failed：现转为 Agent `ModelRetry`。
- HTTP 402 曾使 run failed：现为 `provider_blocked + needs_resume`。
- `needs_resume` 曾没有用户继续入口：现有同 run Resume API 与前端按钮。
- 执行中取消曾可能同时产生 `run_failed`：现由专用取消分支保持单一语义。
- 影子 Gate 11 曾永久没有样本：30 条语料中现有 2 条受控主动取消场景。
- 缺少酒店时曾在规划阶段才失败：现于任何地图查询前提出具体锚点问题。
- `clarify` obligation 曾没有用户问题出口：现于任何 provider 查询前生成具体问题。
- `merge` obligation 曾被当作查询 skip：现与根 obligation 共享一个 CandidateSet、selected
  candidate 和最终 stop。
- 非即时可恢复的余额阻断曾在一次影子 invocation 内重复尝试：现只记录一次并等待人工 Resume。

## 尚不能判定完成

以下证据不是代码可以自行伪造的：

- 当前代码版本 30 条真实 provider 运行全部完成；
- 2 条主动取消场景在真实模型调用期间完成；
- 多轮用户地点／住宿确认后仍复用同一 run 并得到正确方案；
- 人工逐日执行性复核达到约定通过率；
- 连续 7 天无 P0/P1。

当前 Flash 分布在第 16 条收到 HTTP 402 后停止。上述真实缺陷已增加离线回归边界；剩余外部
分布由用户明确豁免。2026-08-01 的工程切流与 V1/V2 删除已经完成，但完整 30-run 与 7 天稳定性
仍为未执行，而非通过。
