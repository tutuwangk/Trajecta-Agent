import test from "node:test";
import assert from "node:assert/strict";

import {
  coverageStatusLabel,
  deliveryHeading,
  experienceStatusLabel,
  factStatusLabel,
  interleaveTimeline,
  providerBlockerMessage,
  runStatusLabel,
} from "./agent-v3-view.ts";
import type { AgentV3Delivery } from "./agent-v3-types.ts";

function delivery(overrides: Partial<AgentV3Delivery>): AgentV3Delivery {
  return {
    run_id: "run-current",
    run_status: "succeeded",
    delivery_state: "not_delivered",
    candidate: null,
    assessment: null,
    release: null,
    ...overrides,
  };
}

test("delivery copy never calls cancelled or review-required output a release", () => {
  assert.equal(
    deliveryHeading(delivery({ run_status: "cancelled" })),
    "本次运行已取消，未交付",
  );
  assert.equal(
    deliveryHeading(delivery({ delivery_state: "review_required" })),
    "方案待调整",
  );
  assert.equal(
    deliveryHeading(
      delivery({
        delivery_state: "publishable",
        release: {
          release_id: "release-current",
          producing_run_id: "run-current",
          candidate_snapshot_id: "candidate-current",
          status: "published",
          immutable: true,
          narrative: {
            overview: "当前运行事实说明。",
            days: [
              {
                day_number: 1,
                title: "第一天",
                summary: "09:00 从酒店出发。",
              },
            ],
          },
          published_at: "2026-08-01T00:00:00Z",
        },
      }),
    ),
    "已核验发布方案",
  );
});

test("coverage labels expose the complete disposition loop", () => {
  assert.equal(coverageStatusLabel("scheduled"), "已安排");
  assert.equal(coverageStatusLabel("pending_confirmation"), "待确认");
  assert.equal(coverageStatusLabel("not_scheduled"), "不安排");
  assert.equal(coverageStatusLabel("unhandled"), "未处置");
});

test("run, fact, experience and provider states have actionable Chinese semantics", () => {
  assert.equal(runStatusLabel("needs_resume"), "已暂停，可继续");
  assert.equal(runStatusLabel("failed"), "运行失败，未发布");
  assert.equal(factStatusLabel("degraded"), "存在事实缺口");
  assert.equal(experienceStatusLabel("needs_adjustment"), "需要调整");
  assert.equal(
    providerBlockerMessage("provider_balance_insufficient"),
    "模型账户余额不足；补充余额后可从当前检查点继续。",
  );
  assert.match(providerBlockerMessage("provider_authentication_failed"), /认证/);
  assert.match(providerBlockerMessage("provider_quota_exhausted"), /额度/);
});

test("timeline rows preserve explicit stop-leg-stop sequence", () => {
  const rows = interleaveTimeline(
    [
      {
        stop_id: "hotel",
        candidate_id: "cand-hotel",
        obligation_ids: [],
        name: "酒店",
        kind: "lodging",
        arrival_at: "2026-08-01T09:00:00",
        departure_at: "2026-08-01T09:00:00",
        stay_duration_min: 0,
      },
      {
        stop_id: "meal",
        candidate_id: "cand-meal",
        obligation_ids: ["obl-meal"],
        name: "陈麻婆豆腐",
        kind: "meal",
        arrival_at: "2026-08-01T09:20:00",
        departure_at: "2026-08-01T10:20:00",
        stay_duration_min: 60,
      },
    ],
    [
      {
        leg_id: "leg-1",
        from_stop_id: "hotel",
        to_stop_id: "meal",
        departure_at: "2026-08-01T09:00:00",
        arrival_at: "2026-08-01T09:20:00",
        duration_min: 20,
        mode: "taxi",
        fact_id: "fact-1",
        fact_source: "amap",
        fact_status: "verified",
      },
    ],
  );

  assert.deepEqual(rows.map((row) => row.kind), ["stop", "leg", "stop"]);
  assert.equal(rows[2]?.kind, "stop");
  if (rows[2]?.kind === "stop") {
    assert.equal(rows[2].value.name, "陈麻婆豆腐");
  }
});

test("coordinate projection preserves stop numbers and rejects missing or invalid points", async () => {
  const { projectMapStops, mapNavigationUrl } = await import("./agent-v3-view.ts");
  const base = { candidate_id: "c", obligation_ids: [], kind: "visit", arrival_at: "09:00", departure_at: "10:00", stay_duration_min: 60 };
  const stops = [
    { ...base, stop_id: "a", name: "东点", location: { longitude: 104.07, latitude: 30.66 } },
    { ...base, stop_id: "b", name: "无坐标" },
    { ...base, stop_id: "c", name: "西点", location: { longitude: 104.05, latitude: 30.66 } },
    { ...base, stop_id: "bad", name: "无效", location: { longitude: NaN, latitude: 30.66 } },
  ];
  const points = projectMapStops(stops);
  assert.deepEqual(points.map((point) => point.number), [1, 3]);
  assert.ok(points[0].x > points[1].x);
  assert.ok(points.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y)));
  assert.equal(mapNavigationUrl(stops[1]), null);
  assert.ok(mapNavigationUrl(stops[0])?.includes("coordinate=gaode"));
});

test("timeline never attaches an unrelated route by array position", () => {
  const stops = [{ stop_id: "a", candidate_id: "c", obligation_ids: [], name: "a", kind: "visit", arrival_at: "09:00", departure_at: "10:00", stay_duration_min: 60 }];
  assert.equal(interleaveTimeline(stops, [{ leg_id: "wrong", from_stop_id: "x", to_stop_id: "y", departure_at: "10:00", arrival_at: "11:00", duration_min: 60, mode: "walk", fact_id: "f", fact_source: "demo", fact_status: "estimated" }]).length, 1);
});

test("GCJ-02 display conversion matches known Beijing case and leaves overseas points intact", async () => {
  const { gcj02ToWgs84 } = await import("./agent-v3-map.ts");
  const point = gcj02ToWgs84(116.404, 39.915);
  assert.ok(Math.abs(point.longitude - 116.3977555) < .000001);
  assert.ok(Math.abs(point.latitude - 39.9135957) < .000001);
  assert.deepEqual(gcj02ToWgs84(-.1276, 51.5072), { longitude: -.1276, latitude: 51.5072 });
});

test("map projection fits markers into viewport while retaining the source location", async () => {
  const { fitMapView, mapStops, screenPoint, mercatorPoint } = await import("./agent-v3-map.ts");
  const base = { candidate_id: "c", obligation_ids: [], kind: "visit", arrival_at: "09:00", departure_at: "10:00", stay_duration_min: 60 };
  const stops = [
    { ...base, stop_id: "a", name: "a", location: { longitude: 104.07, latitude: 30.66 } },
    { ...base, stop_id: "b", name: "b", location: { longitude: 104.02, latitude: 30.64 } },
  ];
  const original = JSON.stringify(stops);
  const points = mapStops(stops);
  const view = fitMapView(points, 600, 470);
  for (const point of points) {
    const screen = screenPoint(point, view, 600, 470);
    assert.ok(screen.x >= 60 && screen.x <= 540);
    assert.ok(screen.y >= 80 && screen.y <= 390);
  }
  assert.equal(JSON.stringify(stops), original);
  assert.deepEqual(mercatorPoint(0, 0), { x: .5, y: .5 });
  assert.ok(Number.isFinite(mercatorPoint(180, 90).y));
});

test("OSM requests only intersecting current viewport tiles and clips global bounds", async () => {
  const { viewportTiles, screenPoint, TILE_SIZE } = await import("./agent-v3-map.ts");
  const view = { x: .5, y: .5, zoom: 12 };
  const tiles = viewportTiles(view, 600, 470);
  assert.ok(tiles.length <= 12);
  for (const tile of tiles) {
    const origin = screenPoint({ x: tile.x / 2 ** tile.zoom, y: tile.y / 2 ** tile.zoom }, view, 600, 470);
    assert.ok(origin.x < 600 && origin.x + TILE_SIZE > 0);
    assert.ok(origin.y < 470 && origin.y + TILE_SIZE > 0);
    assert.match(tile.url, /^https:\/\/tile\.openstreetmap\.org\/12\/\d+\/\d+\.png$/);
  }
  for (const edge of [{ x: 0, y: 0, zoom: 2 }, { x: 1, y: 1, zoom: 2 }]) {
    assert.ok(viewportTiles(edge, 600, 470).every((tile) => tile.x >= 0 && tile.y >= 0 && tile.x < 4 && tile.y < 4));
  }
});

test("map sequence segments preserve selectable stop IDs and interrupt at a coordinate gap", async () => {
  const { consecutiveMapPairs } = await import("./agent-v3-map.ts");
  const points = [{ number: 1, stop: { stop_id: "a" } }, { number: 3, stop: { stop_id: "c" } }, { number: 4, stop: { stop_id: "d" } }];
  const pairs = consecutiveMapPairs(points);
  assert.deepEqual(pairs.map((pair) => [pair.from.stop.stop_id, pair.to.stop.stop_id]), [["c", "d"]]);
  assert.equal(pairs[0].to, points[2]);
});
