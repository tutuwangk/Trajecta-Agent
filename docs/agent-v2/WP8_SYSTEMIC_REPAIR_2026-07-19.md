# WP8 系统性修复执行记录

日期：2026-07-19

## 当前状态（2026-07-23）

Flash MVP Gate 已通过：六个核心场景最新 attempt 均 `published`，结构化产品检查 6/6，
internal failed、unhandled、incomplete 和 deterministic fallback 均为 0；p50 365.77 秒、
p95 484.22 秒。生产默认仍为 V4 Pro，Flash 只用于降低 WP8 实链成本。六个 Release 均为
`degraded / needs_adjustment`，30 次分布、匿名盲测和 7 天观察仍未完成，因此 cutover Gate
未评估、不得进入 WP9。下文按时间保留失败、修复和复验依据。

## 2026-07-22 WP8 失败后系统性收口

本轮不再把延迟归因为单一的“预算太小”，而是同时修复写入幂等、episode
预算重置、Provider 容量突发、低层工具扩张、硬约束漂移和验收器漏计数。

已落地：

- hypothesis/candidate/source/claim 采用观察与合并语义。Candidate 以
  `(hypothesis_id, provider, provider_place_id)` 作为身份合同；Claim 以排除
  `claim_id/acquired_at` 的内容指纹在 Workspace 内幂等复用。
- `start/resume/recover/convergence/provider retry` 共用持久化 `RuntimeBudget`、
  `RunUsage` 和 provider attempt 计数。临时模型故障只自动续跑一次；无 checkpoint
  时进入可重试 `incomplete/transient_external`，内部不变量错误明确进入
  `failed/internal`。
- 高德请求通过共享 `ProviderRequestCoordinator` 限制为 1 QPS、最多 2 个在途请求；
  日配额或缺少配置会打开 circuit。批量搜索、地点事实、游览画像、路线和
  draft hydrate 均返回逐项失败与 retryability，不再因单项失败炸掉整个 Run。
- Tool metadata 集中管理；convergence 只保留 Workspace 读取、draft hydrate/语义修补、
  模拟、澄清和提交。Agent retries 收紧为 `tools=2/output=3`。
- `CompletionEvaluator` 仅阻断日期缺失、空 touring day、实体/时间线不变量和用户明确
  不可调整的 HARD commitment。STRONG must-visit/餐厅/酒店缺失进入
  `experience_status`，不阻止一份诚实、可执行的路线发布。
- WP8 runner 完整统计所有终态、failure class、首次 provider 错误、自动恢复、
  checkpoint 发布、cache hit 和 circuit；结构化澄清 fixture 在同一 `run_id` 恢复。
  产品断言先将用户名称映射到 hypothesis，再以 resolution candidate ID 验证 Draft；
  名称子串不再直接判定成功。Runner 支持 `--repetitions 5` 形成 6 次冷缓存和
  24 次共享缓存的 30 次分布，并以 `0/1/2` 区分 Gate 通过、产品失败和环境阻断。
- Mention 实链工件现在保留 span、role、polarity、route relevance 和 priority，
  正向实体 precision 与 polarity accuracy 分开报告。

当前本地证据：后端全量 `393 passed`，V2 分层测试 `95 passed`，数据集
`100/60/50/30/20` 通过，Python `compileall`、前端 `18 passed`、TypeScript、Next webpack
production build 和 `git diff --check` 均通过。在 30 次分布、盲测和 7 天观察完成前，
WP8 仍不得判定为通过。

当前版本北京单场景预检已实际执行，DeepSeek 在 0.79 秒返回 `402 Insufficient
Balance`。Run 正确终止为 `failed/permanent_external`、`retryable=false`，provider attempt
为 1，无自动重试、无 unhandled exception、无 deterministic fallback，runner 返回
`environment_blocked=true`。该结果验证了环境失败语义，不能验证路线质量；本轮因此未继续
六场景和 30 次实链。工件位于 `/tmp/trajecta-wp8-systemic-20260722`。

## 2026-07-23 充值后实链修复与六场景回归

充值后首先复跑北京：577.73 秒发布，3 天、故宫和八达岭稳定实体断言通过，首 Draft
187.98 秒、首 checkpoint 461.09 秒。随后六场景回归又暴露并修复了三个非供应商根因：

1. Provider 调用计数被放入错误 batch 作用域，成都触发
   `name 'candidate_ids' is not defined`。计数现在分别在地点事实、游览画像和路线 batch
   的真实请求边界执行，并由集成测试核对每类 logical provider call。
2. 上海产品断言用子串把“外滩”同时映射为“上海外滩英迪格酒店”。验收器现在优先沿
   `GoalCommitment.subject_hypothesis_id -> PlaceResolution.candidate_id` 获取期望实体，
   只对没有 commitment 的诊断标签做规范化精确名称匹配。
3. `reserve_ratio=30%` 原来没有切分墙钟，初始 episode 可占满全部预算，导致 10 分钟
   到期时没有时间运行 compact convergence。Runtime 现在在前 70% 墙钟结束探索 episode，
   保留后 30% 给只开放 hydrate、语义 patch、模拟、澄清和提交的独立收敛 episode；
   `start/resume/recover` 共用该持久化预算窗口，澄清恢复会把答案摘要带入紧凑续跑。

上海在修复后以 385.11 秒发布，首 Draft 146.34 秒、首 checkpoint 295.63 秒；相比一次
600.06 秒预算耗尽尝试，减少约 28 万 input tokens，并在预算前半段形成可发布候选。
北京当前批次以 390.03 秒发布，较充值后首次成功缩短约 32%，首 Draft 140.87 秒、首
checkpoint 320.02 秒。

厦门曾经复现的重复 candidate 写入路径本轮以冷缓存执行：11 个 hypothesis、51 个候选，
268.12 秒发布，没有 `place candidate ids must be unique`。持久化 Draft 将鼓浪屿安排
300 分钟并与岛外主路线分天。重庆 4 日在 292.89 秒发布，洪崖洞夜间窗口及两个必去实体
通过；杭州 3 日在 284.36 秒发布，路线按西湖、灵隐/茶博、运河/河坊街分区。

杭州同时暴露父景区覆盖合同缺口：`西湖` commitment 解析为父景区实体，而 Draft 安排的
断桥残雪和曲院风荷均以高德 `parent_provider_place_id` 精确指向该父实体。新增的确定性覆盖
语义只允许“已排子景点证明到访父景区”，不允许父景区反向冒充指定子景点；酒店和餐厅仍
要求精确实体。杭州已发布快照使用当前代码复算后，西湖/灵隐寺产品断言和
`strong_commitment_omitted` 均通过。

六个场景各自最近一次可用路线结果均为 Agent-authored `published`，产品断言经当前代码为
6/6，耗时为 201.55 / 385.11 / 390.03 / 268.12 / 292.89 / 284.36 秒；该六值的
p50 为 288.62 秒，p95 为 390.03 秒。全部没有 unhandled exception、internal failed、
deterministic fallback、false verified 或 `spatial_estimate -> verified`。发布事实状态仍多为
`degraded`，体验状态除成都外多为 `needs_adjustment`，因此这些结果证明稳定发布与核心语义，
不等同于盲测体验 Gate 已通过。

在父子覆盖修复后准备重新生成杭州工件时，DeepSeek 再次于 0.74 秒返回
`402 Insufficient Balance`。该新 attempt 正确记录为 `failed/permanent_external`、不重试且
无 Draft/Release；旧发布工件没有被覆盖。由于账户余额再次成为环境阻断，本轮没有继续消耗
请求凑 30 次分布，也没有执行 shadow 盲测或 7 天观察。WP8 仍未达到 WP9 切流 Gate。

当前本地证据更新为：后端全量 `397 passed`，数据集 `100/60/50/30/20`，Python
`compileall`、`git diff --check`、前端 `18 passed`、TypeScript 和 Next webpack production
build 全部通过。六场景审计工件位于 `/tmp/trajecta-wp8-six-20260723`；充值后北京预检位于
`/tmp/trajecta-wp8-systemic-20260722`。

## 2026-07-22 项目级同步复核

本次复核以当前工作区代码为准：后端全量 `377 passed`，数据集校验为
`mention=100 / grounding=60 / fact_claim=50 / trip=30 / runtime=20`，Python
`compileall`、前端 `18` 项测试、TypeScript 和 Next webpack production build 均通过。

2026-07-19 之后的实现状态保持为：来源 artifact 与 Workspace observation 幂等分离、候选搜索
缓存只复用真实 provider 结果、tool-effect 以 `(run_id, tool_call_id)` 隔离、指定餐饮进入严格
Draft 实体、checkpoint 发布前重新读取当前事实并增量 hydrate。上述本地结果不等于供应商容量或
WP8 总 Gate 通过；当前仍缺少当前版本 30 次全链分布、同输入 V2/Legacy 盲测和连续 7 天稳定期，
不得切流或删除 Legacy。

## 当前结论

本轮针对 WP8 第二次验收暴露的两类真实故障进行了系统性修复，而不是只扩大预算：

- `source_records.source_record_id` 重复写入不再导致未处理异常。
- 成都、上海、北京真实场景均能形成 Agent-authored 完整 Draft 和 checkpoint；本轮观察到的三次最终实链均发布，未使用 deterministic itinerary fallback。
- 上海两日从此前 `0 draft + 32 tools exhausted` 改为 2/2 天发布，279.25 秒；外滩实际在 19:30，指定餐厅进入严格餐饮实体和路线。
- 北京三日发布但耗时 724.75 秒。该轨迹随后暴露重复事实 hydrate 和旧 checkpoint 忽略新事实的风险，相关代码已继续修复；尚未用修复后的版本重跑北京，因此不能宣称长场景性能 Gate 已通过。

WP8 总 Gate 仍未完成：没有执行 30 次当前版本实链、匿名盲测和 7 天稳定观察，因此不允许切流或删除 Legacy。

## 根因与修复

### 1. 来源不是 Workspace 私有行

同一个高德结果会被不同 mention、搜索或 Workspace 再次观察。旧表把稳定的 `source_record_id` 直接作为 Workspace 行主键，导致真实重复事实触发唯一键异常。

新存储拆为：

- `source_artifacts`：全局内容寻址来源。
- `workspace_source_observations`：Workspace 对来源的观察引用。

同内容来源幂等复用；相同 ID、不同内容才是冲突。旧 `source_records` 在启动时迁移，只读兼容但不再接收新写入。Claim 同 ID、同 Workspace、同内容也幂等复用。

### 2. Mutation 与 tool effect 同事务

Workspace、事实、发布 mutation 现在可在同一个 SQLite 事务内记录 tool effect。恢复不再依赖“业务写成功后再补 journal”的窄窗口。版本令牌仍由 Runtime/Repository 内部持有，不暴露给模型。

### 3. 承诺关联实体，不匹配展示名称

旧完成度检查使用字符串判断承诺是否满足，导致“成都太古里亚朵酒店”已正确解析为“成都太古里亚朵S酒店”后仍被判为缺失，Agent 反复改草案直到耗尽预算。

`GoalCommitment.subject_hypothesis_id` 现在直接关联地点假设，再通过 `PlaceResolution` 的真实 candidate ID 判断覆盖。旧 Workspace 通过 hypothesis 名称恢复实体关联，不再退化为 provider 展示名的模糊匹配。反例会返回具体缺失 commitment，而不是笼统的 `core_commitment_missing`。

### 4. Anytime planning 由 Harness 支撑

新增：

- 批量地点搜索与批量解析。
- 批量地点事实与批量游览画像。
- `hydrate_draft_context`：根 Agent 先创建完整 Draft，再一次补齐已排入路线的地点事实、画像和方向性路线。
- 完整性检查：精确旅行天数/日期、空 touring day 和明确不可调整的 HARD 承诺覆盖。
- `CandidateCheckpoint`：只有结构模拟和完整性同时通过才保存。

预算不再只是总次数：前 70% 可探索，后 30% 收敛；草案前候选扩张有软上限；两份完整 checkpoint 后停止重开路线并要求提交。按行程规模的墙钟上限为 1 天 480 秒、2 天 600 秒、3 天及以上 720 秒；request/tool 上限提高到 40/128，仅作为安全尾部，不能替代收敛。

### 5. Hydrate 增量补缺

真实北京轨迹曾对同一批候选 hydrate 五次，累计 305 条 claims。现在工具先检查已有：

- 对应日期的 operational facts。
- visit profile fields。
- 精确方向和交通方式的 route duration。

仅查询缺口；全部已有时返回 `draft_context_reused`，不增加 Workspace/fact version。该行为已由重复 hydrate 回归测试覆盖。

### 6. Checkpoint 不得绕过新事实

旧 checkpoint fallback 只使用保存时的 claim IDs。若之后发现地点闭馆，仍可能发布旧候选。

现在 fallback 会把 checkpoint Draft 放回当前 Workspace，并使用当前全部 claims、当前 Workspace version 和当前 fact version 重新运行 Compiler 与 CompletionEvaluator。新增测试证明：checkpoint 后发现 `known_closed` 时不会产生 Release。

### 7. 指定餐厅成为严格路线实体

`DraftMeal` 新增 `place_candidate_id` 和 `travel_mode_from_previous`。确定性 Compiler 现在验证餐厅身份、解析状态、营业事实和前往餐厅的路线；hydrate 也把餐饮锚点纳入事实与路线查询。普通灵活用餐仍可不锚定，不新增发布硬规则；但已解析的指定餐厅若被省略，会进入 `requested_meal_place_omitted` experience issue，不能再用 prose 冒充已安排。

### 8. WP8 指标不再把 Release 等同产品通过

`run_trip_agent_v2_wp8.py` 现在记录：

- 期望天数是否完整。
- must-visit 是否实际进入 visit/meal entity。
- 夜间窗口是否体现在编译后的 start minute。
- `first_progress_seconds`、`first_draft_seconds`、`first_checkpoint_seconds`。
- 结构化 `product_issue_codes`。

### 9. 外部地图容量与跨 Run 幂等

最终北京复跑在候选搜索阶段收到高德 `CUQPS_HAS_EXCEEDED_THE_LIMIT`。工具边界现在把搜索异常转换为结构化 `place_provider_unavailable`，不会再穿透为未处理异常；Repository 增加 30 天真实候选搜索缓存，按目的地和 mention 身份复用 provider ID、来源与候选，降低重复配额消耗。缓存只复用真实 provider 结果，不能凭空生成新城市/新地点。

该回归还发现 tool effect 旧表只以 `tool_call_id` 为主键，不同 Run 若复用 provider call ID 会错误回放。新表使用 `(run_id, tool_call_id)` 复合键并迁移旧记录；跨 Workspace 缓存测试证明第二个 Run 可复用候选，同时所有 mutation 仍按本 Run 执行。

## 已执行验证

| 检查 | 当前结果 |
|---|---|
| 后端全量 | `377 passed` |
| Python compileall | 通过 |
| 前端测试 | `18 passed` |
| TypeScript | 通过 |
| Next production build | 通过 |
| 成都 1 日 | `published`，410.86 秒，无异常/fallback，必去地点保留 |
| 上海 2 日 | `published`，279.25 秒，2/2 天，外滩 19:30，餐饮实体化 |
| 北京 3 日 | `published`，724.75 秒，3/3 天，两个强地点和两顿指定餐饮保留 |

北京结果来自增量 hydrate 与 checkpoint 当前事实复验落地前的诊断运行，仅证明稳定发布路径，不代表当前性能分布。成都结果也表明单日延迟仍可能超过 p95 目标。

使用最终代码复跑北京时，高德账户配额已耗尽，57.22 秒以结构化 `failed` 工件结束，未产生草案或 Release。候选缓存与结构化工具失败随后已由本地合同覆盖，但由于当前外部配额状态，本轮无法再次完成最终版本北京实链；早先 724.75 秒成功不得替代该外部 Gate。

## 下一步 Gate

1. 用当前代码至少重跑北京一次，确认增量 hydrate 后 claim 数和耗时显著下降，且不会发布被当前事实否决的 checkpoint。
2. 执行当前六场景一轮；任何 unhandled、错误天数、必去缺失或夜间窗口失败均先修复，不用增加重复运行稀释。
3. 再执行 30 次可审计实链并统计 published/incomplete/failed、p50/p95、首次 provider error、checkpoint fallback 和 fact/experience issue。
4. 生成同输入 Shadow 与匿名盲测；完成 Gate 和 7 天稳定观察后才能进入 WP9。

## 2026-07-23 Flash MVP 验收模式

为控制当前 MVP 阶段的真实模型成本，WP8 runner 默认使用
`deepseek-v4-flash` 作为唯一 root `TripPlannerAgent` 的模型档位。该配置只作用于
`run_trip_agent_v2_wp8.py`：root Agent 的指令、工具权限、持久化、预算和发布合同保持不变，
生产代码继续默认使用 `deepseek-v4-pro`。每个 attempt 记录 `planner_model_variant` 与
`planner_model`，同一输出目录禁止混入不同模型档位，避免将 Pro/Flash 结果合并为一个分布。

验收同时保留两套 Gate：

- `mvp`：必须覆盖六个核心场景；至少 5/6 发布且通过实体、天数和时间窗产品检查；剩余一次只能
  是诚实的 `incomplete`，不能是 `failed`、未处理异常或内部错误；deterministic fallback 为
  0；p50 不超过 420 秒、p95 不超过 720 秒，且不突破按天数设置的硬墙钟。
- `cutover`：继续保留 30 次分布、published ≥95%、incomplete <5%、预算耗尽 <2%、
  p50 ≤300 秒、p95 ≤600 秒和早期 Draft/checkpoint 指标。MVP 通过不代表允许进入 WP9。

当前本地回归为后端 `400 passed`，Python compileall、前端 `18 passed`、TypeScript、
Next webpack production build 和 `git diff --check` 均通过。

充值通知后使用全新目录 `/tmp/trajecta-wp8-flash-mvp-20260723` 对北京三日场景执行了两次
Flash canary。两次都在首个模型请求分别于 0.70 秒和 0.82 秒返回 DeepSeek
`402 Insufficient Balance`，实际模型字段均为 `deepseek-v4-flash`。Runtime 正确记录为
`failed / permanent_external / retryable=false / external_account_unavailable`，没有产生
工具调用、Draft、Release、内部异常或 fallback；第二次 attempt 保留
`previous_attempt_id`，没有覆盖首次失败证据。

因此当前仍是环境阻断，不能继续六场景实链，也不能宣称 MVP Gate 通过。待当前项目使用的
DeepSeek API key 对应账户余额可用后，应使用新目录获得干净的六场景首轮分布：

```bash
backend/.venv/bin/python backend/scripts/run_trip_agent_v2_wp8.py \
  --output-dir /tmp/trajecta-wp8-flash-mvp-rerun \
  --planner-model flash \
  --gate-profile mvp
```

### 额度恢复后的 Flash 六场景结果

DeepSeek 额度恢复后，Flash canary 首先暴露了验收 fixture 对模型生成 `question_id` 的错误依赖：
同一组“便宜坊/炸酱面分店”问题在不同 attempt 中生成不同 ID，导致 runner 无法回答并停在
`waiting_user`。fixture 现改为按稳定的语义实体分别匹配问题；未知实体仍保持
`waiting_user_unresolved`，不会用全局默认答案吞掉新问题。修复后北京在同一业务 `run_id`
中完成回答和恢复。

同一 Flash 验收目录最终保留 8 次 attempt：北京两次旧 fixture `waiting_user` 诊断记录，以及
六个核心场景各自最新的成功发布。MVP Gate 按每个 case 的最新 attempt 计算，历史失败/等待记录
继续用于审计而不覆盖。

| 场景 | 最新终态 | 耗时 | 产品检查 |
|---|---|---:|---|
| 成都 1 日 | `published` | 194.29 秒 | 通过 |
| 上海 2 日 | `published` | 484.22 秒 | 通过 |
| 北京 3 日 | `published` | 430.82 秒 | 通过；同 run 澄清恢复 |
| 厦门 2 日 | `published` | 303.71 秒 | 通过 |
| 重庆 4 日 | `published` | 293.78 秒 | 通过 |
| 杭州 3 日 | `published` | 427.84 秒 | 通过 |

六场景最新分布为 `published=6/6`、`failed=0`、`incomplete=0`、内部错误 0、
deterministic fallback 0、硬墙钟超限 0；最新结果 p50 为 365.77 秒，p95 为
484.22 秒。天数、稳定候选身份、must-visit 与显式时间窗检查 6/6 通过，MVP Gate 为
`true`。

MVP 通过不代表体验已经完成：六个 Release 均为 `fact_status=degraded` 与
`experience_status=needs_adjustment`。主要软问题是营业事实缺口、长日、部分饭点偏差；
北京仍有 `route_spatial_estimate`，重庆有 `requested_meal_place_omitted`。这些问题按最小
硬约束原则没有阻止可执行路线发布，但应作为下一轮体验优化和真实用户盲测输入。

Flash 还暴露了成本与上下文效率问题：北京 root 输入累计约 103 万 tokens，杭州约 210 万
tokens。耗时并不随旅行天数线性增长，而更受重复上下文和收敛尾部影响。下一阶段应优先压缩
provider transcript/Workspace 摘要、让第二个有效 checkpoint 更早触发提交；不应继续扩大
request/tool 上限。30 次切流分布、匿名盲测和 7 天观察仍未执行，因此 `cutover` Gate 未评估，
不得进入 WP9。
