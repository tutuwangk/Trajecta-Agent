# Meal Slot Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把午餐、晚餐升级为路线骨架里的正式约束，让真实餐饮地点、场内用餐和就近补位共享同一真相源，并让时间线按该真相源渲染。

**Architecture:** 后端在骨架层新增 `meal_slots`，由 LLM 决定每天需要哪些用餐节点以及由哪个真实餐饮地点或兜底方式承载；归一化层只负责把 `meal_slots` 落成 `meal_breaks` 与地点标签，不再按饭点硬插“午餐/晚餐”；前端继续只消费归一化后的 itinerary，但改为优先展示绑定到真实餐饮地点的用餐语义。

**Tech Stack:** FastAPI, pytest, Next.js, TypeScript

---

### Task 1: 锁定后端骨架与归一化真相源

**Files:**
- Modify: `backend/tests/test_planner.py`
- Modify: `backend/tests/test_itinerary_normalizer.py`
- Modify: `backend/tests/test_verifier.py`

- [ ] **Step 1: 先写 planner 的失败测试**
- [ ] **Step 2: 运行相关 pytest，确认因缺少 `meal_slots` 失败**
- [ ] **Step 3: 再写 normalizer / verifier 的失败测试**
- [ ] **Step 4: 再次运行相关 pytest，确认失败原因正确**

### Task 2: 实现后端 meal slots 工作流

**Files:**
- Modify: `backend/app/agents/planner.py`
- Modify: `backend/app/agents/itinerary_normalizer.py`
- Modify: `backend/app/agents/verifier.py`
- Modify: `backend/app/prompts/plan_itinerary.md`

- [ ] **Step 1: 给 planning context 和 skeleton 增加 `meal_slots` / `meal_candidates`**
- [ ] **Step 2: 让 itinerary materialization 保留 `meal_slots`**
- [ ] **Step 3: 重写 normalizer 的用餐生成逻辑，只消费 `meal_slots`**
- [ ] **Step 4: 重写 verifier，用“slot 是否被满足”替代“有没有餐厅”**
- [ ] **Step 5: 更新提示词，让 LLM 判断早餐/午餐/晚餐适配与顺路性**

### Task 3: 适配前端类型与时间线

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/components/DayTimeline.tsx`

- [ ] **Step 1: 扩充 itinerary 类型，支持 `meal_slots`、餐饮地点承载信息**
- [ ] **Step 2: 调整时间线渲染，真实餐厅承载用餐时不再重复插 generic 午餐/晚餐**
- [ ] **Step 3: 保留 `inside_poi` 与 `fallback_nearby` 的显示语义**

### Task 4: 回归验证

**Files:**
- Test: `backend/tests/test_planner.py`
- Test: `backend/tests/test_itinerary_normalizer.py`
- Test: `backend/tests/test_verifier.py`
- Test: `backend/tests/test_planning_workflow.py`
- Test: `frontend/lib/types.ts`
- Test: `frontend/components/DayTimeline.tsx`

- [ ] **Step 1: 运行相关后端 pytest 子集**
- [ ] **Step 2: 运行后端全量 pytest，确认没有流程回归**
- [ ] **Step 3: 运行 `pnpm run typecheck`，确认前端类型闭环**
