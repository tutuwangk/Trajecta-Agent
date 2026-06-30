# AGENTS.md

## 项目定位

这是本地可跑的旅行路线整理 MVP。前端让用户输入旅行需求和旅行笔记，后端使用真实 LLM 和高德 Web 服务整理地点、确认地点、计算路线并生成可调整的每日行程。

## 技术栈

- 前端：`frontend/`，Next.js App Router，TypeScript，Tailwind CSS。
- 后端：`backend/`，FastAPI，Pydantic，SQLite。
- 验证：后端用 `pytest`，前端用 `pnpm run typecheck` 和 `pnpm run build`。

## 关键规则

- 不读取或提交 `.env`；使用 `.env.example` 说明环境变量。
- 第一版不使用 mock 路线。缺少真实 `LLM_API_KEY` 或 `AMAP_API_KEY` 时，应返回可读错误。
- 用户确认过且地图地点确认成功的地点，才能进入正式路线。
- 手动改地点名称后，必须重新做地点确认。
- 行程强度按“从酒店出发到晚上回到酒店”的全部外出时间计算，不按地点数量计算。
- 强度上限：`躺平式旅游` 5 小时以内，`常规` 9 小时以内，`特种兵` 14 小时以内。
- 外出总时长包括酒店往返交通、点间交通、用餐、排队、游玩和休息。
- 前端可见文案面向普通旅行用户，避免展示 `POI`、匹配度、后端端口、会话、Agent 等开发者词汇。

## 常用命令

```bash
backend/.venv/bin/python -m pytest
cd frontend && pnpm run typecheck
cd frontend && pnpm run build
```

当前受限沙箱里，Next/Turbopack 构建可能因创建进程和绑定端口被拒绝失败；非沙箱环境可验证生产构建。

## 文档入口

- `README.md`：安装、启动、使用流程和验证。
- `docs/ARCHITECTURE.md`：模块结构、数据流和强度计算规则。
- `docs/HANDOFF.md`：当前交接状态、验证结果和后续注意事项。
