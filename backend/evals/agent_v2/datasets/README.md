# Agent V2 评测数据集

每个文件使用 JSONL，一行一个独立 case；同类数据允许使用 `*_cases_*.jsonl` 分片。
`case_id` 永久稳定；修正标签时递增 `label_version`，不得重用 ID 表达不同问题。

| 文件 | 任务 | WP0 目标 | 当前 seed |
|---|---|---:|---:|
| `mention_cases.jsonl` | 地点提及召回与字符区间 | 100 | 10 |
| `grounding_cases.jsonl` | 实体身份、父子层级与歧义 | 60 | 6 |
| `fact_claim_cases.jsonl` | 来源、claim 类型与发布资格 | 50 | 6 |
| `trip_cases.jsonl` | 端到端产品场景 | 30 | 6 |
| `runtime_cases.jsonl` | 状态、恢复、幂等与故障注入 | 20 | 8 |

Seed cases 只建立高风险合同，不代表 WP0 标注规模 Gate 已通过。`needs_fixture_refresh=true` 的候选 ID 来自既往真实复现，必须用本轮脱敏 provider fixture 重新冻结后才能进入自动 precision 统计。

使用 `backend/scripts/validate_agent_v2_datasets.py` 检查所有分片的数量、schema 与 case ID
唯一性。Synthetic contract 可以证明边界逻辑，不能替代真实 V4 Flash/高德预测统计。

通用字段：

- `case_id`：稳定 ID。
- `label_version`：标签版本。
- `source`：`live_2026_07_18`、`prior_reproduction`、`product_decision` 或 `synthetic_contract`。
- `input`：被测输入。
- `expected`：可机器断言的预期。
- `notes`：非评分说明。
