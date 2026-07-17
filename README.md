# Trajecta-Agent

把零散旅行资料和想法整理好，给你一份每日行程规划。

Trajecta-Agent 面向真实旅行决策场景。用户输入目的地、日期、天数、交通偏好、路线目标和行程强度等信息，并粘贴攻略、地点清单、餐厅推荐或自由描述后，系统会整理地点、确认位置，并生成可继续调整的每日路线。


## 功能简介

- 收集目的地、出发日期、天数、酒店名、兴趣偏好、交通偏好、路线目标和行程强度
- 识别资料里的景点、餐厅、酒店和其他地点
- 生成轻量地点池，让用户用少量操作完成关键决策
- 生成按天拆分的旅行路线
- 在结果页继续修改，让 Agent 重新整理
- 连锁品牌可先做“顺路规划”，先选参考地点，再匹配最近的具体门店

适合的输入包括攻略摘录、餐厅清单、朋友推荐、酒店地址、备忘录，以及自然语言形式的旅行想法。

## Agent 工作流程

1. 收集行程约束
   用户填写目的地、出发日期、天数、酒店、偏好、交通方式、路线目标和行程强度，并补充攻略、地点清单、餐厅推荐或自由描述。日期会进入逐日营业事实判断；当前不收集尚未真正参与决策的预算和人数。

2. 解析资料、确认地点并整理地点池
   资料解析角色从文本中识别地点和用户意图，优先保证不漏掉简称、混合名称和未指定门店的品牌；高德服务再负责确认真实位置。系统结合资料证据、地点类型、酒店位置与用户偏好给出默认建议，用户可将地点设为必去、待定、移除或改名。

   对未指定门店的连锁品牌，用户可以选择“顺路规划”，以酒店或其他已识别地点为参考，系统匹配最近的具体门店后再参与路线规划。

3. 由唯一的 `PlannerAgent` 主动决策
   只有已确认、且未被移除的地点会进入正式规划。系统先把用户原文整理为 `IntentLedger`（意图账本）和 `PlanningEnvelope`（规划包络）：固定预约是受保护的高权重锚点，明确分天、普通时段、指定餐厅和用户提及地点按强弱偏好参与整体取舍。PlannerAgent 使用严格的 `PlannerTurn` 协议：当营业时间或停止入场时间会改变路线取舍时，可先发出一次 `NeedFacts`；系统通过高德与有界网页搜索解析带日期、来源和置信度的事实，再由同一个 Agent 提交 `PlanBlueprint`。Agent 负责选点、分天、排序、饭点策略和取舍，但不能创建地点、坐标、营业状态或交通耗时。

   规划会综合天数、酒店位置、交通偏好、路线目标、兴趣偏好和行程强度。地图事实、地点合法性、分钟级时间线、业务校验和发布资格始终由 Python 代码掌握。

4. 系统编译、校验、有限修正并决定发布状态
   后端根据实际采用的相邻地点请求高德交通信息，确定性生成酒店往返、点间交通、饭点、到达时间和每日总外出时长。系统优先保护明确要求和必去地点；当节奏或空间安排不够理想时，会自动调整或给出提示，而不是把普通取舍交还给用户。

   生成结果会经过事实与时间线校验。每次规划最多包含一次事实请求、一次首版蓝图和一次整体修正；普通时段、餐饮、跨天顺序、明确预约和舒适度作为不同权重的评价目标反馈给 Agent，不再逐条阻断。模型修正后仍前重后空时，系统只比较一次容量受控的确定性分天候选，不增加 Agent 循环。已知闭馆、地点/路线事实造假、时间算术错误和 14 小时绝对上限等最小不变量才阻断发布；未能满足的预约会作为高优先级体验冲突明确展示。地图或网页事实不足时事实状态为带来源说明的 `degraded`，不能伪装成 `verified`。

5. 根据反馈继续调整
   用户可以在结果页删除地点、调整先后顺序、时段或节奏，也可以重新做连锁店的顺路规划。酒店、天数、交通偏好和原始资料属于规划输入，需要回到行程设置修改并重新生成。若重新识别地点、改名重搜或修改地点决定，旧路线会标记为需要重新生成，避免继续展示过期结果。

## 本地部署

需要本地安装：

- Python 3
- Node.js
- pnpm

复制 `.env.example` 为 `.env`，填入必要配置：

- `LLM_API_KEY`：LLM 服务密钥
- `LLM_MODEL`：默认模型
- `AMAP_API_KEY`：高德 Web 服务密钥

可按角色分别设置 `LLM_DURATION_MODEL`、`LLM_PLANNING_MODEL`、`LLM_FACT_MODEL` 和 `LLM_COPY_MODEL`；未设置时均使用 `LLM_MODEL`。POI 营业事实搜索由 `POI_WEB_SEARCH_URL` 和 `POI_WEB_SEARCH_TIMEOUT_SECONDS` 控制，搜索失败会形成事实缺口而不是终止整次规划。其他运行参数请参考 `.env.example` 和 [技术说明](./docs/ARCHITECTURE.md)。

后端会自动读取项目根目录的 `.env`。

启动后端：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

启动前端：

```bash
cd frontend
pnpm install
pnpm run dev
```

浏览器打开 `http://localhost:3000` 即可开始使用。

规划接口以任务方式运行：`POST /sessions/{session_id}/plan` 返回 `run_id`，前端通过运行记录恢复和轮询。结果同时展示事实状态 `verified/degraded/failed` 与体验状态 `good/needs_adjustment/conflict`，避免用“事实已核验”掩盖路线取舍；断线或进程重启后可用原 `run_id` 请求恢复，不会重复创建同一幂等任务。

## 跨电脑或线上部署补充

当前仓库默认面向**本地前后端一起运行**。如果前端部署到另一台电脑、云主机或托管平台，需要额外补一项后端地址配置：

- 在前端运行环境中设置 `BACKEND_API_BASE_URL=https://你的后端地址`
- 不要只部署静态前端页面；`/api/backend/*` 依赖 Next.js 服务端代理，纯静态托管不会转发到 FastAPI
- 如果浏览器会直接访问 FastAPI，而不是先经过前端代理，再把后端 `CORS_ORIGINS` 改成实际前端地址

如果不配置 `BACKEND_API_BASE_URL`，前端在本地开发时会默认转发到 `http://127.0.0.1:8000`；但在其他电脑或线上环境，这个地址通常只会指向部署机器自己的本地回环地址，最终表现为“请求失败”。

## 项目结构

- `frontend/`：前端页面、组件和前端代理
- `backend/`：FastAPI、唯一 PlannerAgent、领域模型、编排器、确定性编译和发布门禁
- `database/`：数据库结构参考
- `docs/`：实施方案、当前技术架构和验收报告
- `data/`：本地 SQLite 运行数据，不应提交或分享

## 当前边界

- 当前版本以本地运行和完整规划闭环为重点
- 用户资料仍是地点来源；营业时间等决策事实可由 Agent 按需请求网络补充
- 地图能力以高德地点链接为主
- 同一 `run_id` 会复用事实快照、蓝图和已通过门禁的编译结果；从 release gate 恢复时只重做文案与保存。多实例 exactly-once 不在本版范围

完整架构、状态机、错误码和指标定义见 [技术架构](./docs/ARCHITECTURE.md)，本轮实测结果见 [软优化与六场景验收](./docs/AGENT_SOFT_OPTIMIZATION_ACCEPTANCE_2026-07-17.md)，此前架构收口记录见 [Agent 重构验收报告](./docs/AGENT_REFACTOR_ACCEPTANCE.md)。

## 验证

```bash
backend/.venv/bin/pytest -q
backend/.venv/bin/python -m compileall -q backend/app backend/main.py
cd frontend && pnpm test
cd frontend && ./node_modules/.bin/tsc --noEmit --incremental false
cd frontend && pnpm exec next build --webpack
```
