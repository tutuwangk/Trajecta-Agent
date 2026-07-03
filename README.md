# 旅行路线整理 v0.2

这是一个本地可跑的旅行路线整理工具。用户填写目的地、天数、酒店名、出行人数、预算、兴趣偏好、交通偏好、路线目标和行程强度，粘贴旅行资料后，系统会识别地点、自动整理地点池，再生成可查看、可调整的每日路线。

当前版本是本地 MVP：前端使用 Next.js，后端使用 FastAPI，本地 SQLite 保存会话、地点、路线和修改历史。路线生成依赖真实 LLM 与高德 Web 服务，不提供 mock 路线。

首页表单会把目的地、天数、酒店名、出行人数、预算、交通偏好、路线目标和行程强度作为结构化 `user_profile` 提交，避免酒店名等关键字段只靠自然语言解析。路线结果页会按每天显示代码绘制的纵向时间线，帮助用户直观看到出发、到站、停留和点间交通预留时间。

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

1. 首页填写目的地、天数、酒店名、出行人数、预算、兴趣偏好、交通偏好、路线目标和行程强度。
2. 在“资料与地点”中粘贴攻略、笔记、地点清单、餐厅推荐、酒店地址或旅行想法。
3. 点击“识别地点”，系统会匹配地图位置并生成轻量地点池。
4. 可选择少量调整：必去、待定、移除、改名；不操作也可以直接生成路线。
5. 点击“生成路线”。
6. 查看路线概览、每日路线、外出总时长、未安排地点、需要确认的地点和出行提醒。
7. 每日路线中查看“今日时间线”，快速确认出发、到站、停留和交通预留时间。
8. 使用快捷按钮或自然语言继续调整路线。

## 行程强度规则

行程强度按“从酒店出发到晚上回到酒店”的全部外出时间计算，不按地点数量计算。外出总时长包括酒店往返交通、点间交通、用餐、排队、游玩和休息。

- `躺平式旅游`：5 小时以内。
- `常规`：9 小时以内。
- `特种兵`：14 小时以内。

后端优先读取每日路线里的 `total_outing_min`。没有该字段时，会累加 `hotel_departure_transport_min`、`hotel_return_transport_min`、`meal_breaks`、每个地点的 `duration_min` 和 `transport_to_next.duration_min`。

前端时间线只根据路线 JSON 绘制，不调用 LLM 生成图形。到达时间会按 5 分钟向后取整，点间交通会额外保留少量机动时间，避免把旅行时间展示得过死。

## 后端接口

- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/recognize-places`
- `POST /sessions/{session_id}/extract-pois`
- `POST /sessions/{session_id}/place-overrides`
- `PATCH /sessions/{session_id}/pois`
- `POST /sessions/{session_id}/plan`
- `POST /sessions/{session_id}/revise`

`/recognize-places` 和 `/place-overrides` 是当前主流程接口；`/extract-pois` 和 `PATCH /pois` 保留为兼容入口。

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
