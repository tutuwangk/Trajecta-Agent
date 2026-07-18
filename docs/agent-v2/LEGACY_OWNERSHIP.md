# Legacy 所有权与迁移表

> 日期：2026-07-18
> 规则：V2 可以复用外部 adapter 与经等价测试的纯计算，但不得导入 Legacy 决策模块。

| Legacy 区域 | 当前职责 | V2 处置 | 删除时点 |
|---|---|---|---|
| `app/agents/planner_agent.py` | 两请求/两工具的旧 PlannerAgent、字符串 JSON 边界 | 全量重写为 `trip_agent/agent/planner.py` | V2 切流稳定期后 |
| `app/agents/planning_workflow.py` | 固定事实→蓝图→编译→修正→fallback 流程 | 禁止导入；由开放工具循环替换 | V2 切流稳定期后 |
| `app/agents/planner.py` | Context、materializer、Legacy LLM helper | 决策删除；仅可迁移经等价测试的纯时间物化计算 | V2 切流稳定期后 |
| `app/agents/poi_extractor.py`、`ugc_reader.py` | 固定地点抽取前置流程 | 由按需 MentionExtractor 工具替换 | V2 切流稳定期后 |
| `app/services/poi_grounder.py` | 候选评分、类别覆盖、实体选择 | 禁止复用决策；搜索原始结果经 V2 adapter 复用 | V2 切流稳定期后 |
| `app/agents/visit_duration_estimator.py` | 模型估时加语义 fallback | 由 VisitProfile tool 替换；确定性范围校验保留 | V2 切流稳定期后 |
| `app/agents/schedule_evaluator.py` | 候选评分与容量规则 | 删除；体验由 Agent 权衡，模拟器只返回反例 | V2 切流稳定期后 |
| `app/agents/itinerary_normalizer.py` | 自动补餐、修时间和归一化 | 禁止用于 V2 修路；仅迁移无决策格式函数 | V2 切流稳定期后 |
| `app/agents/reviser.py`、`planning/copy_merger.py` | 发布前后文案与事实指纹 | V2 只保留 ReleaseGate 后受限文案与指纹校验 | 等价测试完成后迁移 |
| `app/planning/feasibility_compiler.py` | 时间编译但反向依赖 Legacy normalizer/planner | 以 V2 Simulator 重写；可迁移纯算术 | V2 切流稳定期后 |
| `app/planning/release_gate.py` | Legacy 双状态发布门禁 | 按最小硬约束重写，不直接复用规则集合 | V2 切流稳定期后 |
| `app/planning/poi_fact_resolver.py` | Web 事实提取与应用 | Web client 可复用；claim/source 合同重写 | V2 切流稳定期后 |
| `app/orchestration/planning_orchestrator.py` | 固定 workflow facade | 禁止导入，由 Domain Harness 替换 | V2 切流稳定期后 |
| `app/api/routes.py` | Session wizard、POI 前置、plan/resume/revision 混合 facade | 新建 V2 router；影子期并存，不在旧文件继续加 V2 schema | V2 切流稳定期后 |
| `app/services/database.py` | Session/POI/PlanningRun 混合存储且依赖 Agent 决策 | 新建 V2 repositories；Legacy store 只供旧链路/历史 adapter | 执行代码切流后收缩 |
| `app/services/amap_client.py` | 高德 HTTP adapter | 通过 V2 port 复用 | 保留 |
| `app/services/web_search.py` | Web HTTP adapter | 通过 V2 port 复用 | 保留 |
| `app/services/route_service.py` | 路线和 spatial estimate 计算 | 外部查询/纯计算可迁移；事实类型重写 | 等价测试后保留/迁移 |
| `app/services/cache_service.py` | 通用缓存 facade | 仅在不携带决策语义时复用 | 视实现保留 |
| `frontend/components/POIConfirmTable.tsx`、`PlacePool.tsx` | 规划前置人工确认 | 改为 Workspace 可视投影，不作为开跑门槛 | V2 默认后删除旧交互 |
| `frontend/components/PlanningInterventionCard.tsx` | 单问题 intervention | 由 1～5 题 ClarificationBatch 替换 | V2 默认后删除 |
| `frontend/lib/planning-recovery.ts` | Legacy polling/resume | 由 AgentRun event/recovery client 替换 | V2 默认后删除 |
| `frontend/components/ItineraryCard.tsx` 等展示 | 最终路线展示 | 与旧流程解耦后复用 | 保留 |

## 架构断言

自动测试必须扫描 `backend/app/trip_agent/` 的 AST/import graph，拒绝对 planner/workflow/intent ledger/poi extractor/schedule evaluator/normalizer/poi grounder/Legacy orchestrator 的任何导入。
