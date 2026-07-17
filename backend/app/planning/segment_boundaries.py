from __future__ import annotations


def hotel_rest_boundary_pairs(day: dict) -> set[tuple[str, str]]:
    """Return adjacent POI pairs separated by an explicit hotel-rest segment."""

    boundaries: set[tuple[str, str]] = set()
    segments = day.get("segments") or []
    for index, segment in enumerate(segments):
        if segment.get("kind") != "hotel_rest":
            continue
        previous_outing = _nearest_outing_segment(segments, index, -1)
        next_outing = _nearest_outing_segment(segments, index, 1)
        if not previous_outing or not next_outing:
            continue
        origin = str((previous_outing.get("poi_ids") or [None])[-1] or "").strip()
        destination = str((next_outing.get("poi_ids") or [None])[0] or "").strip()
        if origin and destination:
            boundaries.add((origin, destination))
    return boundaries


def _nearest_outing_segment(segments: list[dict], start_index: int, step: int) -> dict | None:
    index = start_index + step
    while 0 <= index < len(segments):
        segment = segments[index]
        if segment.get("kind") == "outing":
            return segment
        index += step
    return None
