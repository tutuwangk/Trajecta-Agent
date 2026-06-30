# 旅行路线整理 v0.2

这是一个本地可跑的旅行路线整理工具。用户填写目的地、天数、酒店区域、预算、兴趣偏好和行程强度，粘贴旅行笔记后，系统会整理笔记里的地点，让用户确认地点，再生成可查看、可调整的每日路线。

当前版本是本地 MVP：前端使用 Next.js，后端使用 FastAPI，本地 SQLite 保存会话、地点、路线和修改历史。路线生成依赖真实 LLM 与高德 Web 服务，不提供 mock 路线。

## 项目结构

- `frontend/`：Next.js App Router 前端页面。
- `backend/`：FastAPI 后端、路线生成与校验逻辑、LLM 和高德服务封装。
- `database/`：后续迁移 PostgreSQL/Supabase 时可复用的表结构。
- `examples/`：样例输入和输出结构。
- `data/`、`backend/data/`：本地 SQLite 数据和缓存，默认不提交。
- `docs/`：架构、运行和交接说明。

## 环境变量

复制 `.env.example` 为 `.env`，填写真实密钥：

```env
LLM_API_KEY=your_deepseek_api_key
LLM_MODEL=deepseek-chat
AMAP_API_KEY=your_amap_web_service_key
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

缺少 `LLM_API_KEY` 或 `AMAP_API_KEY` 时，对应步骤会失败并返回可读错误。

## 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a
source ../.env
set +a
uvicorn main:app --reload --port 8000
```

## 启动前端

```bash
cd frontend
pnpm install
pnpm run dev
```

打开 `http://localhost:3000`。

## 使用流程

1. 首页填写目的地、天数、酒店区域、同行人、预算、兴趣偏好和行程强度。
2. 粘贴一篇或多篇旅行笔记。
3. 点击“开始整理”。
4. 在路线页确认、删除、设为必去、设为可选，或手动修正地点。
5. 点击“生成 / 更新路线”。
6. 查看每日路线、外出总时长、路上时间、地点安排、未安排原因和出行提醒。
7. 使用快捷按钮或自然语言继续调整路线。

## 行程强度规则

行程强度按“从酒店出发到晚上回到酒店”的全部外出时间计算，不按地点数量计算。外出总时长包括酒店往返交通、点间交通、用餐、排队、游玩和休息。

- `躺平式旅游`：5 小时以内。
- `常规`：9 小时以内。
- `特种兵`：14 小时以内。

后端优先读取每日路线里的 `total_outing_min`。没有该字段时，会累加 `hotel_departure_transport_min`、`hotel_return_transport_min`、`meal_breaks`、每个地点的 `duration_min` 和 `transport_to_next.duration_min`。

## 后端接口

- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/extract-pois`
- `PATCH /sessions/{session_id}/pois`
- `POST /sessions/{session_id}/plan`
- `POST /sessions/{session_id}/revise`

接口统一返回：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "step_status": {}
}
```

## 验证

后端：

```bash
backend/.venv/bin/python -m pytest
```

前端：

```bash
cd frontend
pnpm run typecheck
pnpm run build
```

在当前受限沙箱中，`pnpm run build` 可能因 Turbopack 创建进程/绑定端口被拒绝而失败；同一代码在非沙箱环境已验证可以构建通过。

## 当前边界

- 不接 Supabase，不做公网部署。
- 不自动爬取小红书，只处理用户粘贴文本。
- 地图展示先提供高德地点链接。
- 排队、营业时间、价格等信息只做提醒，不作为实时事实。
- 前端可见文案面向普通旅行用户；内部类型、组件和后端字段仍保留部分工程命名。
