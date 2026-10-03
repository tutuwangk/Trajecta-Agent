# 用户流程验收 · 2026-10-03

本轮清理已移除恢复请求的未使用参数、完成页规划记录浮层样式、工具证据样式和旧进度行折叠样式。英文 README 为默认入口，中文版本由顶部链接切换。两份 README 同步当前交付规则和完成页功能，保留单 Agent 与确定性 Python 的架构图，并加入当前生产前端展示成都规划的截图。

后端 235 项、前端 20 项测试通过。Python 编译、严格未使用项 TypeScript 检查、Next.js 生产构建和 30 场景、6 城市的语料校验通过。

浏览器验收使用 Chrome、当前 Next.js 生产构建和独立 FastAPI 服务。真实服务流程验证空表单提示、示例日期切换、抽屉 Escape 关闭、手机地图与行程切换、表单提交和活动 Run 刷新恢复。新运行 `run-0b2b902a-e682-43d4-a757-70867ea8616d` 完成需求理解和四个地点的候选查询后，DeepSeek 返回 HTTP 402，原因码为 `provider_balance_insufficient`。Run 进入 `needs_resume`，Release 为空，页面保留进度并显示继续操作。该运行的真实完整规划验收待账号余额恢复后继续。

独立离线浏览器流程使用现有测试中的 Interpreter、RootAgent、地点和事实夹具，保留生产 API、Runtime、严格领域模型、Repository、时间编译、评估和发布实现。表单提交后完成 Release，刷新恢复已发布结果；行程调整创建新 Run 并完成发布；待确认问题回答后关闭确认表单并发布；取消及刷新后保留 cancelled。测试还验证每日切换、地图选点联动时间轴、放大、缩小、显示全部地点、示意图和手机布局。独立真实流程及离线流程记录中的浏览器运行异常与 API 5xx 数量均为零。

首次连接已有 8000 服务时，立即刷新后的一个 Run 读取请求返回 HTTP 500，随后直连及代理读取均返回 200。该测试 Run 已取消。当前代码的独立服务与临时数据库流程未复现此错误，其根因仍待确认。

README 截图来自已有真实 provider 成都两日 Release，经严格模型读取并在独立临时 Repository 中回放，由当前生产前端重新截图。截图尺寸为 1440 × 900，展示第 2 天、地点选择、每日时间轴与地图。截图已进行视觉检查：内容清晰、地图底图可见、选中地点联动、横向溢出为零。

## 证据

机器记录见 [validation.json](./evidence/2026-10-03-user-acceptance/validation.json)。完成页见 [completed-itinerary-2026-10-03.png](./ui-evidence/completed-itinerary-2026-10-03.png)。截图原始行程见 [round2-cd-two-day-final-delivery.json](./evidence/2026-10-02-round2/round2-cd-two-day-final-delivery.json)。真实受阻 Run 的临时数据库保留在 `/private/tmp/trajecta-user-test-20261003/test.sqlite3`，用于同 Run 恢复。
