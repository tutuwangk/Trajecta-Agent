# 架构说明

## 总览

项目由本地前端和后端组成。前端负责收集旅行需求、展示轻量地点池、展示每日路线和接收修改要求；后端负责解析输入、整理地点、生成默认地点决策、调用地图服务、生成路线、校验路线并保存会话状态。

## 数据流

1. 前端将目的地、天数、酒店名、出行人数、预算、偏好、交通偏好、路线目标、行程强度和旅行资料提交到 `POST /sessions`，同时提交结构化 `user_profile`。
2. 后端优先校验并保存结构化用户画像；缺少结构化画像时，才从原始文本解析兜底。
3. 前端调用 `POST /sessions/{session_id}/recognize-places`。
4. 后端从资料中整理地点，调用高德服务确认地图地点，生成系统默认决策和轻量地点池。
5. 用户可在前端少量调整：必去、待定、移除、改名；也可以不操作直接生成路线。已有地图候选默认视为可用地点，用户发现错误时通过改名重新搜索。
6. 前端调用 `POST /sessions/{session_id}/plan`。
7. 后端只使用最终可安排且已匹配的地点构建路线矩阵，调用 LLM 生成每日路线；LLM 返回如果包在代码块或说明文字里，会由统一 JSON 解析层提取有效 JSON。
8. 后端校验路线，移除未确认、未匹配或已排除地点，补齐摘要、未安排地点和需要确认地点。
9. 前端用每日路线 JSON 绘制纵向时间线，展示出发、到站、停留和点间交通预留时间。
10. 用户通过快捷按钮或自然语言调用 `POST /sessions/{session_id}/revise` 继续调整。

## 核心模块

- `backend/app/agents/input_parser.py`：解析目的地、天数、酒店名、出行人数、预算、偏好、交通偏好、路线目标和行程强度。
- `backend/app/agents/poi_extractor.py`：从笔记结构中聚合地点。
- `backend/app/agents/place_organizer.py`：生成系统默认决策、用户覆盖后的最终决策、地点角色和轻量地点池展示对象。
- `backend/app/services/poi_grounder.py`：调用高德候选结果确认地图地点。
- `backend/app/services/route_service.py`：构建地点间路线矩阵。
- `backend/app/agents/planner.py`：调用 LLM 生成路线 JSON。
- `backend/app/agents/verifier.py`：校验路线可执行性。
- `backend/app/agents/reviser.py`：删除、后置或调整不合规地点。
- `backend/app/agents/intensity.py`：计算行程强度上限和每日外出总时长。
- `frontend/components/DayTimeline.tsx`：根据每日路线 JSON 绘制前端时间线，不依赖 LLM 生成图形。

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

前端时间线使用 `items[].arrival_time`、`items[].duration_min`、`items[].transport_to_next.duration_min` 和酒店出发交通字段绘制。显示时间按 5 分钟向后取整，点间交通会增加少量机动时间，只作为用户理解行程顺序和节奏的可视化，不作为新的后端约束。

## 本地存储

SQLite 默认保存本地会话、地点、路线和修改历史。数据库文件位于 `data/` 或 `backend/data/`，不应提交到 Git。

`pois` 表在原始地点和地图地点之外保存：

- `system_decision`：系统默认判断，取值包括 `include`、`optional`、`needs_confirmation`、`exclude`。
- `user_override`：用户少量覆盖操作，取值包括 `must_include`、`optional`、`remove`、`rename_confirm`、`none`。
- `final_decision`：最终是否进入规划，取值包括 `include`、`optional`、`exclude`、`unresolved`。
- `inferred_role`：后端推断的地点角色，例如 `visit`、`meal`、`hotel`、`transport`。

## 前端语言原则

前端可见文案使用用户语言，例如“识别地点”“资料与地点”“必去”“待定”“移除”“改名”“外出”“节奏”。地点池不展示“需确认 / 已识别”这类地址确认状态；内部代码可以保留工程命名，但不应把 `POI`、匹配度、后端端口、会话等词直接展示给普通用户。
