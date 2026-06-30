# 架构说明

## 总览

项目由本地前端和后端组成。前端负责收集旅行需求、展示地点确认表、展示每日路线和接收修改要求；后端负责解析输入、整理地点、调用地图服务、生成路线、校验路线并保存会话状态。

## 数据流

1. 前端将目的地、天数、酒店区域、预算、偏好、行程强度和旅行笔记提交到 `POST /sessions`。
2. 后端解析用户画像，并保存到 SQLite。
3. 前端调用 `POST /sessions/{session_id}/extract-pois`。
4. 后端从笔记中整理地点，调用高德服务确认地图地点，并保存地点列表。
5. 用户在前端确认、删除、设为必去、设为可选，或手动修正地点。
6. 前端调用 `POST /sessions/{session_id}/plan`。
7. 后端只使用已确认且可安排的地点构建路线矩阵，调用 LLM 生成每日路线。
8. 后端校验路线并修订不合规内容，保存路线状态。
9. 用户通过快捷按钮或自然语言调用 `POST /sessions/{session_id}/revise` 继续调整。

## 核心模块

- `backend/app/agents/input_parser.py`：解析目的地、天数、酒店区域、预算、偏好和行程强度。
- `backend/app/agents/poi_extractor.py`：从笔记结构中聚合地点。
- `backend/app/services/poi_grounder.py`：调用高德候选结果确认地图地点。
- `backend/app/services/route_service.py`：构建地点间路线矩阵。
- `backend/app/agents/planner.py`：调用 LLM 生成路线 JSON。
- `backend/app/agents/verifier.py`：校验路线可执行性。
- `backend/app/agents/reviser.py`：删除、后置或调整不合规地点。
- `backend/app/agents/intensity.py`：计算行程强度上限和每日外出总时长。

## 行程强度

强度字段存放在 `user_profile.constraints.physical_intensity`：

- `low`：躺平式旅游，5 小时以内，`300` 分钟。
- `medium`：常规，9 小时以内，`540` 分钟。
- `high`：特种兵，14 小时以内，`840` 分钟。

外出总时长口径是从酒店出发到晚上回到酒店之间的全部时间，包括酒店往返交通、点间交通、用餐、排队、游玩和休息。

后端优先读取每日路线的 `total_outing_min`。如果没有该字段，会累加：

- `hotel_departure_transport_min` 或 `hotel_to_first_transport_min`
- `hotel_return_transport_min` 或 `last_to_hotel_transport_min`
- `meal_breaks[].duration_min`
- `items[].duration_min`
- `items[].transport_to_next.duration_min`

## 本地存储

SQLite 默认保存本地会话、地点、路线和修改历史。数据库文件位于 `data/` 或 `backend/data/`，不应提交到 Git。

## 前端语言原则

前端可见文案使用用户语言，例如“确认地点”“地图地点”“换一个地点”“外出”“节奏”。内部代码可以保留工程命名，但不应把 `POI`、匹配度、后端端口、会话、Agent 等词直接展示给普通用户。
