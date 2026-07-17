from __future__ import annotations

from urllib.parse import quote


def build_poi_link(poi: dict) -> str:
    name = quote(poi.get("standard_name") or poi.get("name") or "")
    location = poi.get("location", {})
    lng = location.get("lng")
    lat = location.get("lat")
    if lng and lat:
        return f"https://uri.amap.com/marker?position={lng},{lat}&name={name}"
    return f"https://www.amap.com/search?query={name}"


def build_navigation_link(origin: dict, destination: dict, mode: str = "walking") -> str:
    origin_location = origin.get("location", {})
    dest_location = destination.get("location", {})
    if not all([origin_location.get("lng"), origin_location.get("lat"), dest_location.get("lng"), dest_location.get("lat")]):
        return ""
    callnative = "0"
    amap_mode = {"taxi": "driving", "public_transport": "transit"}.get(mode, mode)
    return (
        "https://uri.amap.com/navigation?"
        f"from={origin_location['lng']},{origin_location['lat']},start"
        f"&to={dest_location['lng']},{dest_location['lat']},{quote(destination.get('standard_name', '目的地'))}"
        f"&mode={amap_mode}&policy=1&src=travel-agent&callnative={callnative}"
    )
