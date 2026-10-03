from __future__ import annotations


class CacheService:
    def __init__(self, store):
        self.store = store

    def get_route(self, cache_key: str) -> dict | None:
        return self.store.get_cache("route_cache", cache_key)

    def set_route(self, cache_key: str, value: dict) -> None:
        self.store.set_cache("route_cache", cache_key, value)

    def get_duration(self, cache_key: str) -> dict | None:
        return self.store.get_cache("route_cache", f"duration:{cache_key}")

    def set_duration(self, cache_key: str, value: dict) -> None:
        self.store.set_cache("route_cache", f"duration:{cache_key}", value)
