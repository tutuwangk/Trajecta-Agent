from __future__ import annotations

from datetime import datetime, timezone
import os


class CacheService:
    def __init__(self, store):
        self.store = store

    def get_route(self, cache_key: str) -> dict | None:
        value = self.store.get_cache("route_cache", cache_key)
        if not isinstance(value, dict):
            return None
        cache_meta = value.get("_cache") or {}
        fetched_at = _parse_datetime(cache_meta.get("fetched_at"))
        if fetched_at is None or (datetime.now(timezone.utc) - fetched_at).total_seconds() > _route_ttl_seconds():
            return None
        result = {key: raw for key, raw in value.items() if key != "_cache"}
        original_source = str(cache_meta.get("original_source") or result.get("source") or "unknown")
        result["source"] = "route_cache" if original_source == "amap_direction_api" else original_source
        result["fetched_at"] = fetched_at.isoformat()
        result["cache_metadata"] = {
            "cache_valid": True,
            "original_source": original_source,
            "ttl_seconds": _route_ttl_seconds(),
        }
        return result

    def set_route(self, cache_key: str, value: dict) -> None:
        stored = dict(value)
        stored["_cache"] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "original_source": str(value.get("source") or "unknown"),
        }
        self.store.set_cache("route_cache", cache_key, stored)

    def get_duration(self, cache_key: str) -> dict | None:
        return self.store.get_cache("route_cache", f"duration:{cache_key}")

    def set_duration(self, cache_key: str, value: dict) -> None:
        self.store.set_cache("route_cache", f"duration:{cache_key}", value)


def _route_ttl_seconds() -> int:
    try:
        value = int(os.getenv("ROUTE_CACHE_TTL_SECONDS", "86400"))
    except ValueError:
        return 86400
    return max(60, value)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
