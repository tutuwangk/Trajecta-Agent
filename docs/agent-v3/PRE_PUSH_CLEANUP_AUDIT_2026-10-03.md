# 推送前清理复查 · 2026-10-03

本次先创建本地提交 `52e8166`，保存当前重构、首次清理、澄清修复和交付规则调整，共 60 个文件，新增 1,233 行、删除 783 行。随后对该提交进行清理复查。此次操作完成本地提交和审查，远端推送待执行。

当前生产源码已收敛为 V3。首次清理移除的旧建表文件、预算判断器、旧 API 包装和发布入口均已纳入提交。剩余问题集中在后续界面调整留下的样式、一个参数，以及入口文档描述。建议推送前清理这些残留并同步入口文档。

## 发现与影响

| 位置 | 发现 | 影响与建议 |
|---|---|---|
| `frontend/components/agent-v3/AgentV3WorkspaceApp.tsx:83` | 恢复旅程的 `.catch((reason) => ...)` 已使用固定提示，`reason` 无读取方 | 常规类型检查通过；启用 `noUnusedLocals` / `noUnusedParameters` 后报 TS6133。可移除参数 |
| `frontend/app/globals.css:54`、`:137`、`:149`、`:157`、`:211` | `history-arrow`、`tool-evidence`、`run-history` 样式，以及 `.progress-row` 内 `details` / `summary` 规则已失去对应 DOM | 当前 `AgentProgress` 使用普通进度行，完成页也已移除规划记录浮层。可删除孤立规则，保留共享选择器中的 `.progress-milestone`、`.button-arrow` 和 `.sample-link svg` |
| `README.md:22`、`README.zh-CN.md:20` | 仍说明完成后的地图页面可以展开规划记录 | 当前完成页显示地图、时间轴和预约提醒。应同步说明及示例入口 |
| `README.md:11`、`:89`、`:100`，`README.zh-CN.md:12`、`:85`、`:95`，`docs/ARCHITECTURE.md:8` | 交付说明沿用此前的事实缺口与 verified 描述，未明确当前可交付的普通运营资料缺失情形；流程图仍将 Fact gaps 统一导向 Preview | 当前 `fact_policy.py` 对三类普通运营资料缺失允许交付，并保留 degraded。应说明路线缺口、明确运营冲突及固定预约冲突的处理，以及普通资料不足的交付方式 |
| `docs/agent-v3/ARCHITECTURE.md:144` | 模块清单仍将 `DeliveryPanel` 描述为需求、消歧、事实和门禁交付面 | 同文件第 103 行已说明最终产品页移除内部诊断模块。应将模块清单更新为时间轴、地图和有来源预约事项 |

上述发现具有共同来源：删除产品模块时，相关 CSS 和当前行为文档未同步收尾。下一轮清理应围绕已移除模块核对 DOM、选择器、描述和示例，覆盖这些关联项。

## 文件与调用关系

后端内部静态导入图共有 42 个 Python 模块，从 `backend/main.py` 可到达 41 个。服务入口之外的 `shadow_eval.py` 由 `backend/scripts/run_trip_agent_v3_shadow.py` 和回归测试使用。普通源码的顶层导入扫描未发现闲置导入；单次定义引用候选均属于该评估模块。

V1/V2 旧执行目录、旧路由和跨栈导入的架构测试通过。当前前端源码的导出扫描中，仅被框架读取的 `metadata` 和 `maxDuration` 出现单次引用。前端组件、API、地图、进度和示例模块均有消费入口。

`.btn` 被 `.btn-primary` 和 `.btn-secondary` 的 `@apply` 使用，应保留基础声明。单纯按 JSX class 名称计数会遗漏这种样式依赖。后端依赖和前端依赖均有源码或构建配置消费入口。

提交树共 205 个文件。按路径核对，未发现 `.env`、本地 SQLite、虚拟环境、`node_modules`、`.next`、Python 缓存和 TypeScript 增量缓存进入提交。`.env.example` 已跟踪。历史证据、评估语料、演示素材和样例文件继续承担验收追溯与示例展示用途。

## 验证证据

审查对象为 `52e8166`。后端 `pytest -q`：235 项通过。前端 `pnpm test`：20 项通过。Python `compileall`、常规 TypeScript 类型检查和 `next build --webpack` 均通过。严格未使用项检查复现上述 TS6133，位置为 `AgentV3WorkspaceApp.tsx:83`。

本次检查覆盖提交树、启动导入图、旧执行栈边界、静态引用、样式 DOM 对应关系、依赖用途及当前文档描述。完整 provider 行程验收证据见已有运行记录。

首次清理详情见 [CLEANUP_2026-10-03.md](./CLEANUP_2026-10-03.md)。当前交付规则见 [PRODUCT_POLICY_2026-10-03.md](./PRODUCT_POLICY_2026-10-03.md)、`backend/app/trip_agent_v3/fact_policy.py` 和 `backend/app/trip_agent_v3/delivery.py`；当前完成页见 `frontend/components/agent-v3/DeliveryPanel.tsx` 与 `AgentV3WorkspaceApp.tsx:275`。
