import assert from "node:assert/strict";
import test from "node:test";
import { dayIntensityLabel, outingTimeMinutes, transportTimeMinutes } from "./itinerary-metrics";

test("dayIntensityLabel uses seven and ten hour thresholds", () => {
  assert.equal(dayIntensityLabel(419), "轻松");
  assert.equal(dayIntensityLabel(420), "轻松");
  assert.equal(dayIntensityLabel(421), "常规");
  assert.equal(dayIntensityLabel(600), "常规");
  assert.equal(dayIntensityLabel(601), "特种兵");
});

test("transportTimeMinutes sums every road transport field", () => {
  assert.equal(
    transportTimeMinutes({
      day: 1,
      theme: "",
      summary: "",
      hotel_departure_transport_min: 25,
      hotel_return_transport_min: 35,
      items: [
        {
          time_block: "上午",
          poi_id: "p1",
          name: "IFS",
          duration_min: 90,
          reason: "",
          transport_to_next: { mode: "walking", duration_min: 20 },
        },
        {
          time_block: "中午",
          poi_id: "p2",
          name: "太古里",
          duration_min: 90,
          reason: "",
        },
      ],
    }),
    80,
  );
});

test("outingTimeMinutes fallback does not double count road transport", () => {
  assert.equal(
    outingTimeMinutes({
      day: 1,
      theme: "",
      summary: "",
      hotel_departure_transport_min: 25,
      hotel_return_transport_min: 35,
      meal_breaks: [{ duration_min: 60 }],
      items: [
        {
          time_block: "上午",
          poi_id: "p1",
          name: "IFS",
          duration_min: 90,
          reason: "",
          transport_to_next: { mode: "walking", duration_min: 20 },
        },
        {
          time_block: "中午",
          poi_id: "p2",
          name: "太古里",
          duration_min: 90,
          reason: "",
        },
      ],
    }),
    320,
  );
});
