# Legacy 当前基线

> 采集日期：2026-07-18
> Git：`main @ eabeca1`
> 采集前工作区：clean
> 用途：WP0 对照证据，不代表 Agent V2 已实现

运行环境：Python 3.13.7、Node v24.13.0、pnpm 11.9.0、PydanticAI 2.10.0。六场景 compact summary 没有输出 effective model/prompt version；为避免读取或暴露本地 `.env`，本轮将其记录为“未由验收产物证明”，后续脚本必须以非敏感运行元数据显式返回。

## 技术基线

| 检查 | 本轮结果 |
|---|---|
| `backend/.venv/bin/pytest -q` | 290 passed，4.26s；1 个 Pydantic Graph event-loop deprecation warning |
| `python -m compileall` | 通过 |
| `pnpm test` | 18 passed；存在 Node module-type 非阻断 warning |
| `tsc --noEmit --incremental false` | 通过 |
| `next build --webpack` | 通过；生成 `/`、`/api/backend/[...path]`、`/trip/[sessionId]` |

这些检查证明 Legacy 当前合同稳定，不证明真实供应商链路和路线体验合格。

## 六场景真实链路

命令：

```bash
backend/.venv/bin/python backend/scripts/live_acceptance_six.py --summary-only
```

结果：退出码 1；6 个场景中技术成功 5，产品断言通过 5。

| case ID | 技术 | fact | experience | 耗时 | 关键证据 |
|---|---:|---|---|---:|---|
| `chengdu_1d_low_walk_food` | pass | verified | needs_adjustment | 37.04s | 587 分钟，5 地点；超低强度目标且缺 meal slot |
| `shanghai_2d_medium_night` | pass | verified | needs_adjustment | 46.17s | 第 1 天 688 分钟，超过 540 分钟舒适目标 |
| `beijing_3d_high_culture` | fail | - | - | 35.73s | `plan_capacity_unresolved`；返回 0 天，`day_count:0!=3` |
| `xiamen_2d_low_island` | pass | degraded | needs_adjustment | 45.03s | 第 1 天 421 分钟；事实未达到 verified |
| `chongqing_4d_medium_mixed` | pass | degraded | conflict | 70.97s | 第 2 天 755 分钟、long transfer、两项明确餐饮偏好缺失 |
| `hangzhou_3d_high_nature` | pass | verified | good | 47.58s | 技术与当前产品断言均通过 |

北京场景的实际异常为“必去地点在当前天数内仍然过于拥挤，请增加天数或将部分必去地点改为备选”。这说明容量/体验治理仍能把整条 Legacy 流程堵死；V2 必须通过最小硬约束、Agent 自主舍弃可选地点、早期可行草案和预算预留收敛。

## 已知稳定 case ID

- `LEGACY-LIVE-001`：北京三日容量失败，0 天输出。
- `LEGACY-LIVE-002`：成都低强度仍为 587 分钟且缺正餐槽。
- `LEGACY-LIVE-003`：重庆第 2 天 755 分钟、长转场与明确餐饮偏好缺失。
- `IDENTITY-001`：父实体 IFS 被子商户替换。
- `IDENTITY-002`：杜甫草堂被子景点梅园替换。
- `RUNTIME-001`：重新识别后 stale planning run 不得发布旧地点集合。
- `FACT-001`：`spatial_estimate` 不得进入 verified。
- `INTENT-001`：明确固定预约必须保留原文证据并参与高权重优化。

## WP0 Gate 收口

WP0 的基线冻结已完成。V2 评测集现由高风险真实 seed 与明确标记的
`synthetic_contract` 分片共同组成，实际规模为 mention 100、grounding 60、fact claim 50、
trip 30、runtime 20；`validate_agent_v2_datasets.py` 验证 schema、数量和全局 case ID 唯一性。
Synthetic 分片只证明合同覆盖，不冒充真实供应商精度；真实精度另见
`V4_FLASH_EVAL_2026-07-18.md`。

本基线仍保持不可改写：它证明历史 6/6 结果不可作为当前供应商状态，也不因 V2 后续测试通过
而重写 Legacy 失败事实。
