# Agent V2 WP4-WP7 实施状态

日期：2026-07-18

> 本文件保留 WP4～WP7 阶段验收。后续 WP8 已发现同回合多 mutation 的版本所有权缺口并完成
> R0～R4 修复；当前 Runtime、预算和发布语义以 `WP8_REPAIR_R0_R4_2026-07-18.md` 为准。

## 当前判断

WP4-WP7 已接到同一条隔离 V2 生产链路，阶段内合同与退出门槛已经完成；尚未执行的是明确
属于 WP8 的 30 次全链统计、影子盲测和生产切流 Gate。本文件只记录实际执行证据，不把代码
存在、HTTP 200 或测试绿灯等同于生产切流完成。

## WP4：POI、来源和事实

已完成：

- V4 Flash 地点提及工具；输出只形成 hypothesis，不替根 Agent 消歧。
- 高德候选搜索；每个 candidate 保存真实 provider place ID、parent ID 和原始 SourceRecord。
- V4 Flash 候选比较只提供结构化建议；根 Agent 才能写 PlaceResolution。
- 确定性 identity guard 阻止父实体被子商户/子景点替代，并阻止未指定品牌任意落到分店。
- `resolve_place` 可明确 resolved、ambiguous 或 excluded，Hypothesis 状态与 Resolution 同步。
- Web 来源与 V4 Flash 事实抽取；ObservedClaim 必须引用已保存 SourceRecord。
- 高德方向路线事实；失败时仅生成 `spatial_estimate` EstimateClaim。
- V4 Flash 游览画像 EstimateClaim，不允许成为 verified 事实。
- `source_records` 和 `knowledge_claims` 独立表，fact revision 与 Workspace version 原子递增。
- 阶段实现曾依赖写工具 sequential 与模型回传版本；WP8 证明该合同无法处理同一回复中的多个
  mutation。现已改为 Runtime 内部读取版本、Repository CAS、批量语义工具和 tool-effect 幂等。

修正结构化日期和生产 Narrative 后，真实链路最新一次通过结果：

```text
status=published
elapsed_seconds=108.19
candidate_count=10
source_count=16
claim_count=6
draft_date=2026-08-03
narrative_generator=deepseek-v4-flash-style-selector-v1
all_candidates_have_provider_ids=true
deterministic_fallback=false
```

真实链路先后暴露并修复：mutation 并行 CAS 冲突、V4 Flash 非法结构化 JSON、根 Agent
提前终答、无版本区分的重复读取预算。

评测数据已达到计划规模，并由 `validate_agent_v2_datasets.py` 验证 schema、数量和 case ID 唯一性：

```text
mention=100
grounding=60
fact_claim=50
trip=30
runtime=20
```

原始高风险 seed 与 synthetic contract 分片明确分开；synthetic 数据证明合同覆盖，不冒充真实供应商
精度。生产 V4 Flash adapter 已完成 160-case 全量真实评测：mention recall 100%（158/158），
自动确认 precision 100%（38/38），歧义错误确认率 0%。Grounding 首轮有 1/60 timeout，单 case
重跑成功；供应商首轮错误不能记为零。详见 `V4_FLASH_EVAL_2026-07-18.md`。

残余风险（进入 WP8 继续观测，不阻断 WP4 合同）：

- Mention precision 为 65.83%，说明高召回输出包含城市、否定地点等上下文；这是高召回工具的
  有意边界，根 Agent 必须继续执行 exclude/identity 决策，不能把所有 hypothesis 自动排入 Draft。
- 真实 Web claim 的日期适用性仍需扩大人工审核；当前只有明确适用日期且来源直接支持的 claim
  才能 `release_eligible=true`，其余只形成 degraded 风险，不会产生 false verified。

## WP5：Draft、Compiler 和 ReleaseGate

已完成：

- 严格 WorkingDraft 和版本化高层整稿 mutation。
- 首版可整稿创建；后续支持原子语义操作批次：allocate_day、reorder_cluster、protect_anchor、
  set_visit_window、reduce_intensity、replace_place、remove_optional_place、set_meal_strategy。
- 确定性 `FeasibilityCompiler` 计算顺序、路线耗时、固定时段、日期范围和每日外出时长。
- 酒店出发/返回路线和显式用餐占用进入严格 Draft 与 Compiler；普通饭点仍只是体验目标。
- walking、driving/taxi、transit/public_transport/subway 使用各自高德事实接口；未知模式在 schema
  边界拒绝，不再静默当成 driving。
- 缺失路线事实、未知/未消歧实体、日期越界、固定预约冲突、明确闭馆、空行程和物理超长阻止发布。
- 普通饭点、长日和用餐窗口偏差只进入 `experience_status` 或非阻断 issue。
- `spatial_estimate` 可编译但强制 degraded，绝不 verified。
- 被拒 CandidateSnapshot 与 CandidateRejected 原子冻结；同一 run 最多三次候选提交。
- Candidate、Release、受限 Narrative 与 published 终态合并为单一 SQLite 事务。
- Release fingerprint 同时覆盖 Draft 与 claim 快照。
- ReleaseGate 后 V4 Flash 只选择受限 style enum；所有用户可见文案来自确定性模板，失败时安全降级。
- `acquire_route_facts` 接受最多 20 条 route pair，一次查询批次只推进一个 fact/workspace revision；
  不允许同轮多条路线各自写入并以重试掩盖 stale-version 竞态。

真实两日酒店链路补充验收：

```text
status=published
elapsed_seconds=200.54
candidate_count=30
source_count=41
claim_count=18
draft_day_count=2
hotel_day_count=2
return_hotel_day_count=2
meal_count=2
all_candidates_have_provider_ids=true
deterministic_fallback=false
```

首次执行暴露“同轮多条路线各自 mutation 导致 stale workspace version”，随后按原计划的批量 pair
合同修复并加入回归测试；第二次真实链路通过。骑行未列入 WP0-WP7 的 `TravelMode` 合同，不把未承诺
能力伪装为阶段缺口。

## WP6：Harness 和恢复

已完成：

- 精确锁定 `pydantic-ai-harness==0.7.1`。
- Provider-valid StepPersistence 与领域状态隔离。
- Deferred Tool opaque transcript 独立持久化，同 run 回答恢复已验证。
- 8 分钟墙钟、20 次模型请求、32 次工具调用、30% 收敛预留和版本感知重复签名预算。
- 进展监控依据 blocker 是否下降；连续三次模拟无改善后停止扩展工具，保留修复和提交能力。
- 终答必须对应已成功 publish 的 output validator；不会把模型文本当作发布成功。
- 运行事件持久化，可供前端增量读取。
- 普通崩溃恢复读取同 conversation 的最新安全 snapshot。
- 未知 mutation tool effect 不盲目重放，明确进入 `incomplete/unknown_tool_effect_after_crash`。
- Interruption + waiting_user、Answer + running、Workspace revision + revision run 均为单事务。
- Candidate + Release + Narrative + published 终态单事务，避免半发布。

已验证故障边界：无安全 snapshot、未知 mutation effect、缺失 deferred transcript 不消费答案、
澄清/回答原子边界、候选拒绝上限、原子发布与 revision 幂等。除同进程事务触发器外，新增六类
真实子进程强杀：facts、interruption、answer、revision、candidate rejection、publication；重开
SQLite 后均无半写。published、incomplete、failed、cancelled 四种终态都拒绝迟到工具 mutation。

恢复语义明确为：可重放 read 从安全 snapshot 继续；无法证明幂等的 unknown mutation effect 安全
结束为 `incomplete/unknown_tool_effect_after_crash`，不猜测重放。它满足“显式识别并按工具类型恢复”，
不是把不确定副作用伪装成功。

预算耗尽 `incomplete < 2%`、总 `incomplete < 5%` 和 p50/p95 需要 WP8 的至少 30 次真实全链分布，
当前只证明单日 108.19 秒、两日酒店 200.54 秒均在 8 分钟硬上限内，不提前声称统计 Gate 达标。

## WP7：API 和前端

已完成的新 API：

```text
POST /api/v2/trip-workspaces
GET  /api/v2/trip-workspaces/{workspace_id}
POST /api/v2/trip-workspaces/{workspace_id}/runs
GET  /api/v2/agent-runs/{run_id}
GET  /api/v2/agent-runs/{run_id}/events
POST /api/v2/agent-runs/{run_id}/answers
POST /api/v2/agent-runs/{run_id}/resume
POST /api/v2/agent-runs/{run_id}/cancel
POST /api/v2/trip-workspaces/{workspace_id}/revisions
```

已完成的新首页：

- 原始需求直接创建 Workspace 和 Run，不经过 Legacy POI 确认。
- 运行中禁用主动修改。
- 展示持久化进度事件、地点候选、酒店/用餐占用和当前 Draft。
- 支持 1-5 题 Agent 主动澄清及“其他”答案。
- 终态后以同 Workspace 原子创建 revision run，不改写旧 Release。
- URL 持久化 Workspace/Run；刷新后恢复目标、工作区、进度、Release、Narrative 和 revision 入口。
- 不展示 provider reasoning。

实际技术验证：

```text
frontend: 18 tests passed
TypeScript: passed
Next production build: passed
backend full suite: 354 passed
```

真实浏览器已完成：

- 首页直接创建 V2 Workspace/Run，无 Legacy POI wizard。
- 运行中输入禁用，持续显示 mention、候选、事实、Draft、模拟和发布事件。
- 一次真实 V4 Pro/V4 Flash/高德链路达到 published。
- 带 Workspace/Run 的 URL 刷新后恢复终态、事件、工作区、原始目标和 revision 操作入口。
- 浏览器验收暴露并修复“published 先于 Narrative 可见”的事务竞态和刷新后目标不回填问题。
- revision UI 从旧 published run 原子创建新 run，Workspace v8→v9、URL 切到新 run；取消后旧
  Release 保持不可变且新 run 终态为 cancelled。
- 合法 provider-valid Deferred Tool run 在浏览器显示两题与“其他”输入；答全前提交禁用，答全后
  同一 run 从 waiting_user 恢复 running、URL 不变并产生 run_resumed，随后可安全取消。

边界说明：

- 原生日期控件未被当前浏览器驱动自动写入，但严格日期已由 API 集成测试、刷新回填和两条真实
  provider 脚本（`2026-08-03`）验证；这不是 WP7 用户流程缺失。
- Legacy 已发布行程继续由只读 `/trip/{sessionId}` adapter 展示，未完成运行不迁移；V2 不导入旧
  执行代码。统一视觉外壳不是 WP7 的状态一致性前提。
- 旧 API 和执行代码必须等 WP8 Gate 与至少 7 天稳定期后删除；WP0-WP7 不提前删除，以保留 shadow 对照。

## 是否允许进入下一阶段

WP0-WP7 阶段 Gate 已完成，允许进入 WP8 影子验收；不允许据此直接切流或删除 Legacy。下一阶段
必须取得至少 30 次真实全链的延迟、预算、incomplete、事实与体验分布，并完成匿名盲测和稳定期，
不能把当前两次真实发布或自动测试数量解释为生产替换已经完成。
