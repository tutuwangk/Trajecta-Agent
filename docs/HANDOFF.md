# 交接说明

## 当前状态

截至 2026-06-30，项目已完成一轮前端文案优化和行程强度语义调整。

已完成：

- 前端可见文案从开发者语言改为普通旅行用户语言。
- 首页使用三档行程强度：`特种兵`、`常规`、`躺平式旅游`。
- 强度计算改为外出总时长口径，不按地点数量计算。
- 后端新增 `physical_intensity`，并兼容旧的 `avoid_too_tired`。
- 路线页展示“外出”总时长，并按 5/9/14 小时阈值显示节奏。
- 后端规划提示要求输出 `total_outing_min`、酒店往返时间和用餐时间字段。

## 验证记录

2026-06-30 验证：

```bash
backend/.venv/bin/python -m pytest
```

结果：`21 passed`。

```bash
cd frontend
pnpm run typecheck
```

结果：通过。

```bash
cd frontend
pnpm run build
```

受限沙箱中会因 Turbopack 创建进程/绑定端口失败；非沙箱环境已验证构建通过。

## 后续注意事项

- 如果继续修改行程强度，不要回到“地点数量”规则。
- 如果 LLM 输出没有 `total_outing_min`，后端会退回累加酒店往返、用餐、地点停留和点间交通。
- 如果新增前端文案，先检查是否面向普通用户，避免暴露 `POI`、匹配度、后端端口、会话和 Agent 等词。
- 如果增加新的 API、环境变量或数据表，需要同步 README、`AGENTS.md` 和 `docs/ARCHITECTURE.md`。
