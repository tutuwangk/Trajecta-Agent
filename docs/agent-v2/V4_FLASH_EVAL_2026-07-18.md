# V4 Flash 地点理解与身份评测

日期：2026-07-18

> 历史快照：以下 100% recall 是 WP4 首轮评测加失败 case 重跑后的成功样本结果。后续 WP8
> 首次全量运行出现 8/100 mention provider errors，按“不用重跑覆盖首次失败”的新口径，recall
> 为 91.14%，未达到 95% Gate。当前评分口径和恢复方式以
> `WP8_REPAIR_R0_R4_2026-07-18.md` 为准，本文件不再代表当前供应商稳定性结论。

## 结论

使用生产 `DeepSeekAmapPlaceKnowledge` adapter 对完整数据集执行真实 V4 Flash 调用；按当时允许
失败 case 重跑覆盖的 WP4 口径，三项目标指标通过：

```text
mention cases: 100/100 evaluated
expected mentions: 158
mention recall: 100.00%
mention precision: 65.83%

grounding cases: 60/60 evaluated
automatic confirmations: 38
automatic confirmation precision: 100.00%
ambiguous wrong confirmation rate: 0.00%
```

Grounding 首轮发生 1 次 `Request timed out`，单 case 重跑后成功选择父实体；业务评分采用成功重跑
结果，但首轮供应商错误率仍记录为 `1/60`，不能写成零错误。

## 方法

- 数据规模：100 mention、60 grounding、50 fact、30 trip、20 runtime。
- 高风险历史复现 seed 与 synthetic contract 分片分开保存。
- Mention 直接调用生产 `analyze_mentions`，不使用期望标签构造预测。
- Grounding 直接调用生产 `compare_candidates`；它只给建议，不写 Workspace。
- 评分使用 `validate_agent_v2_datasets.py`，并显式报告 case coverage。
- 原始 provider prediction 保存在本轮临时输出中，不作为产品运行状态或长期用户记忆提交。

## 指标解释

100% recall 达到 WP4 `mention recall >= 95%` Gate。65.83% precision 不是自动确认 precision；高召回
extractor 会保留城市、否定地点和上下文地点，后续仍需根 Agent 使用 `resolve_place(action=exclude)`
或保留歧义。系统不得把全部 hypothesis 自动排入 Draft。

Grounding 的 38 个自动确认全部命中，且所有预期 ambiguous case 均未错误确认，达到：

- 自动确认 precision >= 98%。
- 歧义地点错误自动确认率 <= 1%。
- 父实体不得被子商户或子景点替代。

## 可复现命令

```bash
cd backend
.venv/bin/python scripts/run_agent_v2_live_evals.py \
  --task all --concurrency 3 --output-dir /tmp/agent-v2-eval-full
.venv/bin/python scripts/validate_agent_v2_datasets.py \
  --mention-predictions /tmp/agent-v2-eval-full/mention_predictions.jsonl \
  --grounding-predictions /tmp/agent-v2-eval-full/grounding_predictions.jsonl
```

若存在临时 provider 错误，应只重跑失败 case，并把重跑文件作为后置输入覆盖同 case ID，不得删除
首轮错误记录或把空 prediction 计为正确。
