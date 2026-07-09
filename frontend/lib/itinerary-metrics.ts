import type { DayRoute } from "./types";

export function dayIntensityLabel(outingMinutes: number) {
  if (outingMinutes <= 7 * 60) return "轻松";
  if (outingMinutes <= 10 * 60) return "常规";
  return "特种兵";
}

export function outingTimeMinutes(day: DayRoute) {
  const explicit = day.total_outing_min ?? day.total_outing_minutes ?? day.outing_duration_min ?? day.outing_duration_minutes;
  if (explicit !== undefined) return explicit;
  const itemMinutes = day.items.reduce((sum, item) => sum + (item.duration_min || 0), 0);
  const mealMinutes = (day.meal_breaks || []).reduce((sum, meal) => sum + (meal.duration_min || meal.duration_minutes || 0), 0);
  return itemMinutes + transportTimeMinutes(day) + mealMinutes;
}

export function transportTimeMinutes(day: DayRoute) {
  return (
    firstNumber(day.hotel_departure_transport_min, day.hotel_to_first_transport_min) +
    day.items.reduce((sum, item) => sum + (item.transport_to_next?.duration_min || 0), 0) +
    firstNumber(day.hotel_return_transport_min, day.last_to_hotel_transport_min)
  );
}

function firstNumber(...values: Array<number | undefined>) {
  return values.find((value) => value !== undefined) ?? 0;
}
