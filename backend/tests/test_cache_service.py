from datetime import datetime, timedelta, timezone

from app.adapters.legacy import route_edge_from_legacy
from app.services.cache_service import CacheService


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get_cache(self, table, key):
        return self.values.get((table, key))

    def set_cache(self, table, key, value):
        self.values[(table, key)] = value


def test_route_cache_preserves_precise_source_and_ttl_metadata():
    store = MemoryStore()
    cache = CacheService(store)
    cache.set_route(
        "edge-1",
        {
            "origin_poi_id": "p1",
            "destination_poi_id": "p2",
            "duration_min": 15,
            "source": "amap_direction_api",
        },
    )

    cached = cache.get_route("edge-1")
    typed = route_edge_from_legacy(cached)

    assert cached["source"] == "route_cache"
    assert cached["cache_metadata"]["original_source"] == "amap_direction_api"
    assert typed.confidence == "verified"


def test_route_cache_rejects_expired_fact(monkeypatch):
    store = MemoryStore()
    store.values[("route_cache", "edge-1")] = {
        "origin_poi_id": "p1",
        "destination_poi_id": "p2",
        "duration_min": 15,
        "source": "amap_direction_api",
        "_cache": {
            "fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
            "original_source": "amap_direction_api",
        },
    }
    monkeypatch.setenv("ROUTE_CACHE_TTL_SECONDS", "60")

    assert CacheService(store).get_route("edge-1") is None


def test_route_cache_never_upgrades_spatial_estimate_to_verified():
    store = MemoryStore()
    cache = CacheService(store)
    cache.set_route(
        "edge-1",
        {
            "origin_poi_id": "p1",
            "destination_poi_id": "p2",
            "duration_min": 15,
            "source": "spatial_estimate",
            "degradation_reason": "空间估算",
        },
    )

    cached = cache.get_route("edge-1")
    typed = route_edge_from_legacy(cached)

    assert cached["source"] == "spatial_estimate"
    assert typed.confidence == "estimated"
