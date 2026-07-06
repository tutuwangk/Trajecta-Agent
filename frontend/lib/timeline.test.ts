import test from "node:test";
import assert from "node:assert/strict";

import type { DayRoute } from "./types";
import { buildTimelineEntries } from "./timeline";

test("timeline keeps backend arrival times instead of drifting from previous legs", () => {
  const day: DayRoute = {
    day: 1,
    theme: "成都核心区",
    summary: "",
    items: [
      {
        time_block: "morning",
        poi_id: "p1",
        name: "IFS",
        arrival_time: "09:00",
        duration_min: 60,
        reason: "",
        transport_to_next: { mode: "walking", duration_min: 20, distance_m: 1200 },
      },
      {
        time_block: "late_morning",
        poi_id: "p2",
        name: "太古里",
        arrival_time: "09:45",
        duration_min: 45,
        reason: "",
      },
    ],
    meal_breaks: [],
  };

  const entries = buildTimelineEntries(day);

  assert.equal(entries[1]?.kind, "place");
  assert.equal(entries[1]?.time, "09:00");
  assert.equal(entries[2]?.kind, "transfer");
  assert.equal(entries[2]?.time, "09:25");
  assert.equal(entries[3]?.kind, "place");
  assert.equal(entries[3]?.time, "09:45");
});

test("timeline inserts fallback meal when backend schedules it between two places", () => {
  const day: DayRoute = {
    day: 1,
    theme: "成都核心区",
    summary: "",
    items: [
      {
        time_block: "morning",
        poi_id: "p1",
        name: "武侯祠",
        arrival_time: "10:00",
        duration_min: 60,
        reason: "",
        transport_to_next: { mode: "taxi", duration_min: 15, distance_m: 3200 },
      },
      {
        time_block: "afternoon",
        poi_id: "p2",
        name: "人民公园",
        arrival_time: "13:30",
        duration_min: 60,
        reason: "",
      },
    ],
    meal_breaks: [{ label: "午餐", slot: "lunch", start_time: "12:00", duration_min: 60, source: "fallback_nearby" }],
  };

  const entries = buildTimelineEntries(day);

  assert.equal(entries[2]?.kind, "break");
  assert.equal(entries[2]?.time, "12:00");
  assert.equal(entries[3]?.kind, "transfer");
  assert.equal(entries[3]?.time, "13:15");
  assert.equal(entries[4]?.kind, "place");
  assert.equal(entries[4]?.time, "13:30");
});

test("timeline renders nearby meal copy for non-destination meal breaks", () => {
  const day: DayRoute = {
    day: 1,
    theme: "成都核心区",
    summary: "",
    items: [
      {
        time_block: "midday",
        poi_id: "p1",
        name: "成都博物馆",
        arrival_time: "11:30",
        duration_min: 180,
        reason: "",
      },
    ],
    meal_breaks: [
      {
        label: "午餐",
        slot: "lunch",
        start_time: "12:00",
        duration_min: 60,
        source: "fallback_nearby",
      },
    ],
  };

  const entries = buildTimelineEntries(day);

  assert.equal(entries[1]?.kind, "place");
  assert.equal(entries[1]?.title, "成都博物馆");
  assert.equal(entries[2]?.kind, "break");
  assert.equal(entries[2]?.title, "午餐：就近用餐");
});

test("timeline keeps plain place title when meal happens during a non-restaurant stop", () => {
  const day: DayRoute = {
    day: 1,
    theme: "成都核心区",
    summary: "",
    items: [
      {
        time_block: "midday",
        poi_id: "p1",
        name: "成都博物馆",
        arrival_time: "11:30",
        duration_min: 180,
        reason: "",
      },
    ],
    meal_breaks: [
      {
        label: "午餐",
        slot: "lunch",
        start_time: "12:00",
        duration_min: 60,
        within_poi_id: "p1",
        included_in_item_duration: true,
        source: "inside_poi",
      },
    ],
  };

  const entries = buildTimelineEntries(day);

  assert.equal(entries[1]?.kind, "place");
  assert.equal(entries[1]?.title, "成都博物馆");
  assert.equal(entries[2]?.kind, "break");
  assert.equal(entries[2]?.title, "午餐：就近用餐");
});
