# Agent V3 实施状态（更新于 2026-08-01）

## 结果

V3 已成为唯一运行栈。根前端、后端 API、provider 基础设施、领域模型、Runtime、Repository、
发布门禁、评测和文档均归属 V3；V1/V2 已删除。

## 已完成能力

- 带精确原文 span 的 Requirement Ledger 和地图查询白名单；
- provider 候选进入 Agent 前过滤，每个查询目标最多 3 个；
- 按原始地点分组的选择、理由、置信度和歧义视图；
- 显式地点全量处置闭环，指定餐厅必须为真实 meal stop；
- 显式餐次时段确定性补强，机场航班时间缺失时搜索前澄清；
- 酒店/机场可见锚点与完整 Stop/Leg 时间线，用户地点名拥有展示权；
- 路线事实后编译到达时间，再只查询计划内停靠的运营事实；
- 事实缺口绑定地点、日期、行程影响和建议；
- 单根 Agent 的局部上下文、单向阶段和业务 checkpoint；
- 非法模型提交转为 `ModelRetry`，provider 阻断进入 `needs_resume`；
- RunStatus 与 DeliveryState 分离，Candidate/Assessment/Release 严格绑定 producing run；
- `degraded`、`needs_adjustment`、未闭环覆盖和歧义均不能发布；
- 前端展示处置账本、分组消歧、逐段时间线、事实来源和具体门禁问题。

## 真实 provider 证据

Flash 30 场计划实际启动 16 场，随后按首次 HTTP 402 停止；14 场未启动。13 场形成可评估终态，
5 场产生 Release，人工逐日复核 2/5 通过。真实链路发现的餐次错误、机场时间虚构、provider
别名覆盖用户名称和领域校验错误分类均已增加离线修复与回归测试。

用户在 2026-08-01 明确要求不再执行剩余 14 场，并授权完成 V3 后删除 V1/V2。因此切流已经
执行，但 30-run、人工通过率和 7 天稳定性没有通过，也不会被倒算为通过。原始数据见
[`PARTIAL_DISTRIBUTION_2026-07-31.md`](./PARTIAL_DISTRIBUTION_2026-07-31.md)。

## 当前验证入口

```bash
backend/.venv/bin/pytest -q
backend/.venv/bin/python -m compileall -q backend/app backend/main.py
backend/.venv/bin/python backend/scripts/run_trip_agent_v3_shadow.py \
  --validate-corpus-only --output-dir /tmp/trajecta-v3-corpus-check
cd frontend && pnpm test
cd frontend && ./node_modules/.bin/tsc --noEmit --incremental false
cd frontend && pnpm exec next build --webpack
```
