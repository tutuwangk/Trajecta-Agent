# Chain Nearby Planning Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把连锁餐饮店的顺路规划改成“选参照点后立即落具体门店”的两步流程，并让已落店结果回归普通 POI 工作流。

**Architecture:** 后端新增明确的“确认顺路规划”动作和连锁店 resolved/unresolved 状态，地点池仍以后端白名单控制动作。前端只负责展开参照点选择并提交 `anchor_poi_id`，路线规划主链路不再依赖 `route_dependent_chain` 来为这条交互链选门店。

**Tech Stack:** FastAPI, Pydantic, SQLite, pytest, Next.js, TypeScript

---

### Task 1: 锁定连锁店状态机与地点池动作的失败测试

**Files:**
- Modify: `backend/tests/test_place_organizer.py`
- Modify: `backend/tests/test_database_poi_updates.py`
- Modify: `backend/tests/test_planning_filters.py`

- [ ] **Step 1: 为未落店连锁店写动作白名单测试**

```python
def test_organize_place_keeps_unresolved_chain_without_must_or_optional_actions():
    result = organize_place(raw_poi, grounded_poi, {"constraints": {}})
    assert result["final_decision"] == "unresolved"
    assert result["place_pool_item"]["primary_actions"] == ["顺路规划", "改名", "移除"]
```

- [ ] **Step 2: 为确认顺路规划后的普通 POI 动作写测试**

```python
def test_organize_place_shows_normal_actions_for_resolved_chain_branch():
    result = organize_place(raw_poi, grounded_poi, {"constraints": {}}, user_override="optional")
    assert "必去" in result["place_pool_item"]["primary_actions"]
    assert "待定" in result["place_pool_item"]["primary_actions"]
```

- [ ] **Step 3: 为规划过滤写测试，确认 unresolved 连锁店不会进入正式路线**

```python
def test_planning_grounded_pois_excludes_unresolved_chain_before_branch_selection():
    accepted = _planning_grounded_pois(rows)
    assert accepted == []
```

- [ ] **Step 4: 运行这些测试并确认先失败**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_place_organizer.py backend/tests/test_database_poi_updates.py backend/tests/test_planning_filters.py -q`

Expected: FAIL，失败点集中在旧的 `arrange_nearby` 语义和旧动作白名单

### Task 2: 锁定“确认顺路规划”与回退联动的失败测试

**Files:**
- Modify: `backend/tests/test_chain_arranger.py`
- Modify: `backend/tests/test_database_poi_updates.py`
- Modify: `backend/tests/test_routes_internal.py`

- [ ] **Step 1: 为按参照点匹配最近分店写失败测试**

```python
def test_arrange_chain_to_anchor_resolves_nearest_branch_and_returns_message():
    resolved = arrange_chain_to_anchor(chain_poi, anchor_poi, FakeAmapClient(durations))
    assert resolved["standard_name"] == "星巴克(成都IFS店)"
    assert resolved["chain_status"] == "resolved"
    assert resolved["resolved_from_anchor_poi_id"] == "anchor_ifs"
```

- [ ] **Step 2: 为数据库确认顺路规划写失败测试**

```python
def test_update_poi_decisions_confirms_arrange_nearby_with_anchor():
    store.update_poi_decisions(
        session_id,
        [{"poi_id": "amap_S1", "decision": "confirm_arrange_nearby", "anchor_poi_id": "amap_I1"}],
        arrange_nearby_grounded=resolve_branch,
    )
    row = store.list_pois(session_id)[1]
    assert row["grounded_poi"]["resolved_from_anchor_poi_id"] == "amap_I1"
    assert row["grounded_poi"]["standard_name"] == "星巴克(成都IFS店)"
```

- [ ] **Step 3: 为参照点移除后的自动回退写失败测试**

```python
def test_update_poi_decisions_resets_resolved_chain_when_anchor_removed():
    store.update_poi_decisions(session_id, [{"poi_id": "amap_I1", "decision": "remove"}])
    row = store.list_pois(session_id)[1]
    assert row["grounded_poi"]["chain_status"] == "unresolved"
    assert row["grounded_poi"]["standard_name"] == "星巴克（待选择）"
```

- [ ] **Step 4: 为参照点改成必去后的单向联动写失败测试**

```python
def test_update_poi_decisions_promotes_resolved_chain_when_anchor_becomes_must_include():
    store.update_poi_decisions(session_id, [{"poi_id": "amap_I1", "decision": "must_include"}])
    row = store.list_pois(session_id)[1]
    assert row["user_override"] == "must_include"
    assert row["grounded_poi"]["resolved_from_anchor_poi_id"] == "amap_I1"
```

- [ ] **Step 5: 运行这些测试并确认先失败**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_chain_arranger.py backend/tests/test_database_poi_updates.py backend/tests/test_routes_internal.py -q`

Expected: FAIL，失败点集中在缺少 `anchor_poi_id`、缺少 resolved/unresolved 状态和缺少回退逻辑

### Task 3: 实现后端顺路规划确认动作与联动状态机

**Files:**
- Modify: `backend/app/schemas/models.py`
- Modify: `backend/app/agents/place_organizer.py`
- Modify: `backend/app/services/chain_arranger.py`
- Modify: `backend/app/services/database.py`
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/services/poi_enricher.py`

- [ ] **Step 1: 扩展决策 payload，允许提交 `confirm_arrange_nearby` 与 `anchor_poi_id`**

```python
class PoiDecision(BaseModel):
    poi_id: str
    decision: Literal[..., "confirm_arrange_nearby"]
    manual_name: str | None = None
    anchor_poi_id: str | None = None
```

- [ ] **Step 2: 收紧 place organizer 的连锁店动作白名单**

```python
def _primary_actions(user_override: str, final_decision: str, grounded_poi: dict) -> list[str]:
    if grounded_poi.get("is_chain") and grounded_poi.get("chain_status") != "resolved":
        return ["顺路规划", "改名", "移除"]
    return ["必去", "待定", "移除", "改名"] + (["顺路规划"] if grounded_poi.get("is_chain") else [])
```

- [ ] **Step 3: 实现基于单个参照点的门店解析函数**

```python
def arrange_chain_to_anchor(chain_poi: dict, anchor_poi: dict, amap_client) -> dict:
    selected = min(candidates, key=lambda candidate: _travel_minutes(anchor_poi, candidate, amap_client))
    return {
        **chain_poi,
        "standard_name": selected["name"],
        "amap_id": selected["id"],
        "location": selected["location"],
        "chain_status": "resolved",
        "resolved_branch_id": selected["id"],
        "resolved_branch_name": selected["name"],
        "resolved_from_anchor_poi_id": anchor_poi["poi_id"],
        "resolved_from_anchor_name": anchor_poi["standard_name"],
        "resolved_by": "nearby_anchor",
        "match_status": "matched",
    }
```

- [ ] **Step 4: 在数据库更新链路里处理确认顺路规划、参照点移除回退和参照点变必去同步**

```python
if requested_decision == "confirm_arrange_nearby":
    grounded = arrange_nearby_grounded(raw_poi, grounded, anchor_row, user_profile)
    user_override = _sync_override_from_anchor(anchor_row)
```

```python
if requested_decision == "remove":
    _reset_dependent_chains(connection, session_id, poi_id)
if requested_decision == "must_include":
    _promote_dependent_chains(connection, session_id, poi_id)
```

- [ ] **Step 5: 去掉这条交互链对 `route_dependent_chain` 的正式规划依赖**

```python
def _has_plannable_location(row: dict) -> bool:
    grounded = row["grounded_poi"]
    if grounded.get("match_status") == "matched":
        return True
    ...
```

```python
def _chain_resolution_mode(poi: dict) -> str:
    if poi.get("is_chain") and poi.get("chain_status") != "resolved":
        return "unresolved_chain"
    if poi.get("is_chain"):
        return "user_fixed_branch"
    return "none"
```

- [ ] **Step 6: 运行 Task 1 与 Task 2 的测试，确认变绿**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_place_organizer.py backend/tests/test_chain_arranger.py backend/tests/test_database_poi_updates.py backend/tests/test_planning_filters.py backend/tests/test_routes_internal.py -q`

Expected: PASS

### Task 4: 实现前端二次确认交互与 API payload

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/PlacePool.tsx`
- Modify: `frontend/app/trip/[sessionId]/page.tsx`

- [ ] **Step 1: 扩展前端类型，允许 `anchor_poi_id` 和 resolved 链路字段**

```ts
export type PoiDecisionInput = {
  poi_id: string;
  decision: string;
  manual_name?: string;
  anchor_poi_id?: string;
};
```

- [ ] **Step 2: 扩展 API helper，让地点更新可提交 `anchor_poi_id`**

```ts
export function updatePlaceOverrides(sessionId: string, decisions: Array<{ poi_id: string; decision: string; manual_name?: string; anchor_poi_id?: string }>) {
  return request(...);
}
```

- [ ] **Step 3: 在 `PlacePool` 内实现顺路规划展开区**

```tsx
{showArrangePicker && (
  <div className="mt-3 flex flex-col gap-2">
    <select ...>{anchorOptions.map(...)}</select>
    <div className="flex gap-2">
      <button onClick={() => update(row, "confirm_arrange_nearby", undefined, selectedAnchorId)}>确认顺路规划</button>
      <button onClick={() => setArrangeEditingId("")}>取消</button>
    </div>
  </div>
)}
```

- [ ] **Step 4: 确保 unresolved 连锁店不显示 `必去/待定`，resolved 后恢复普通动作**

```tsx
const canArrangeNearby = item.primary_actions.includes("顺路安排");
const canMarkMust = item.primary_actions.includes("必去");
const canMarkOptional = item.primary_actions.includes("待定");
```

- [ ] **Step 5: 运行前端类型检查**

Run: `cd frontend && pnpm run typecheck`

Expected: PASS

### Task 5: 端到端回归验证

**Files:**
- Test: `backend/tests/test_place_organizer.py`
- Test: `backend/tests/test_chain_arranger.py`
- Test: `backend/tests/test_database_poi_updates.py`
- Test: `backend/tests/test_planning_filters.py`
- Test: `backend/tests/test_routes_internal.py`
- Test: `frontend/components/PlacePool.tsx`

- [ ] **Step 1: 跑后端相关回归集**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_place_organizer.py backend/tests/test_chain_arranger.py backend/tests/test_database_poi_updates.py backend/tests/test_planning_filters.py backend/tests/test_routes_internal.py -q`

Expected: PASS

- [ ] **Step 2: 跑后端全量 pytest**

Run: `backend/.venv/bin/python -m pytest`

Expected: PASS

- [ ] **Step 3: 跑前端生产闭环**

Run: `cd frontend && pnpm run build`

Expected: exit 0；如果受限环境失败，至少保留 `pnpm run typecheck` 的通过证据并据实说明
