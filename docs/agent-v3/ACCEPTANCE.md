# Agent V3 系统性验收映射

| 暴露问题 | V3 责任边界 | 自动证据 |
|---|---|---|
| POI 提炼失控 | span-grounded Requirement Proposal；时间／行动词不进入 place obligations | `test_requirement_compilation.py` |
| POI 筛选缺失 | `QueryPlan` 是 provider 唯一输入 | `test_query_plan.py` |
| 候选消歧不可用 | 每个 target 一个 CandidateSet 和 TargetResolution | `test_grounding.py` |
| 餐饮需求丢失 | meal obligation 只能映射到真实 meal stop | `test_plan_commitment.py`、成都 E2E |
| 显式 POI 静默消失 | 显式可排程地点不能标成 reference/not_a_place；Coverage Report 必须 100% 处置 | `test_requirements.py`、`test_deepseek_adapters.py` |
| 酒店锚点不可见 | 每天首尾必须是 lodging/airport stop | `test_plan_graph.py` |
| 草案不是完整时间线 | Runtime 编译每个 stop 与相邻 leg | `test_timeline_compiler.py` |
| 事实查询浪费 | 路线 FactNeed 只从已提交 draft 派生；运营 FactNeed 只从编译后的真实停靠派生 | `test_fact_needs.py` |
| 来源营业信息被重复否决 | 采用高德或检索返回的营业信息，保留来源引用；普通资料缺失允许交付，明确营业冲突进入调整 | `test_operational_fact_provider.py`、`test_delivery_gate.py` |
| 外部事实失败不可解释 | FactGap 带地点、天数、是否仍在计划和影响 | `test_fact_gap_report.py`、Runtime 故障场景 |
| 公开事实查询转嫁给用户 | 运营工具补搜和读取正文；用户问题限定为私人旅行信息与地点意图 | `test_operational_fact_provider.py`、`test_web_search.py`、`test_autonomous_runtime.py` |
| 回答后旧确认与旧候选残留 | 当前问题随回答关闭；恢复重查失败事实；新输入与新事实形成独立候选；页面淘汰旧请求 | `test_delivery_routes.py`、`test_autonomous_runtime.py`、`agent-v3-view.test.ts` |
| Agent 上下文污染 | 一次一个候选组；Planning 只给 selected summary 与 obligation page；业务 checkpoint 后硬切 provider transcript | `test_local_context.py`、`test_deepseek_adapters.py` |
| Agent 无目标闭环 | 根 Agent 提前结束时由 Autonomous Runtime 转为 `needs_resume`；执行超时保留 checkpoint 与已有终态；相同输入恢复业务 checkpoint | `test_autonomous_runtime.py`、`test_delivery_routes.py` |
| 运行／交付状态脱节 | RunStatus 与 DeliveryState 分离；终态不可覆盖；provider 余额／限流／暂不可用进入 needs_resume 而非伪发布或业务 failed | `test_delivery_gate.py`、`test_repository_state_machine.py`、`test_delivery_routes.py` |
| 当前运行关联错误 | repository/API 只按 producing_run_id 读取 Release | `test_delivery_routes.py` |
| 普通资料缺失卡住整份行程 | 营业来源缺失、资料不全及提取失败允许形成 Release；真实路线、地点身份和固定预约保持执行约束 | `test_delivery_gate.py`、`test_autonomous_runtime.py` |
| 步行上限误判为只能步行 | 步行上限与方式偏好独立保存；驾车可执行，普通偏差为 review | `test_experience_validation.py` |
| 普通分天或时段变成硬门槛 | fixed_commitment 必须有原文证据；普通 required 偏差为 review，固定预约违规或遗漏为 blocking | `test_experience_validation.py`、`test_requirement_compilation.py` |
| 地图坐标由模型编造 | 编译后仅取已选高德候选坐标，展示层转换坐标系，缺失坐标保留原因 | `test_autonomous_runtime.py`、`agent-v3-view.test.ts` |
| 产品页泄漏内部诊断 | 最终页仅展示地图、时间轴、地点及有来源的预约信息，移除诊断与规划记录模块；请求失败显示动作提示 | `agent-v3-view.test.ts`、`agent-v3-api.test.ts`、桌面页面回放 |
| 风险模板化 | DeliveryIssue 带具体 message/recommendation 和地点／天数 | `test_delivery_gate.py` |
| 局部验收失真 | 38 类用户风险场景 + 成都端到端发布旅程；manifest 校验测试函数真实存在 | `backend/tests/trip_agent_v3/evals/scenario_matrix.json` |

## 自动验证入口

```bash
backend/.venv/bin/pytest -q backend/tests/trip_agent_v3
backend/.venv/bin/python -m compileall -q backend/app backend/main.py
cd frontend && pnpm test
cd frontend && ./node_modules/.bin/tsc --noEmit --incremental false
cd frontend && pnpm exec next build --webpack

# 只校验影子语料形状，不调用外部 provider
backend/.venv/bin/python backend/scripts/run_trip_agent_v3_shadow.py \
  --validate-corpus-only --output-dir /tmp/trajecta-v3-shadow
```

## 外部证据限制

- 当前 DeepSeek 与高德账户条件下的真实 30-run 分布；
- 真实地点营业／预约事实覆盖率与 provider 失败率；
- 多轮澄清后 provider transcript 的恢复稳定性；
- 1～5 天不同城市的人工可执行性复核；
- 连续 7 天影子稳定性。

2026-08-01 用户明确终止剩余 14 个真实场景并授权切流、删除 V1/V2。因此当前代码切流已完成，
但上述缺失项仍不得宣称为测试通过；真实 provider 结论仍以部分分布文档为准。
