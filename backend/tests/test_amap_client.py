import httpx
import pytest

from app.core import AppError
from app.services.amap_client import AmapClient


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


def test_amap_client_retries_when_qps_limit_is_exceeded(monkeypatch):
    responses = [
        FakeResponse({"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"}),
        FakeResponse({"status": "1", "pois": [{"name": "武侯祠"}]}),
    ]
    def fake_get(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("app.services.amap_client.httpx.get", fake_get)
    monkeypatch.setattr("app.services.amap_client.time.sleep", lambda seconds: None)

    client = AmapClient(api_key="test-key")

    pois = client.search_poi("武侯祠", city="成都")

    assert pois == [{"name": "武侯祠"}]


def test_amap_client_respects_retry_after_for_429(monkeypatch):
    responses = [
        FakeResponse({}, status_code=429, headers={"Retry-After": "3.5"}),
        FakeResponse({"status": "1", "pois": []}),
    ]
    sleeps = []
    monkeypatch.setattr("app.services.amap_client.httpx.get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr("app.services.amap_client.time.sleep", sleeps.append)

    AmapClient(api_key="test-key").search_poi("武侯祠", city="成都")

    assert sleeps == [3.5]


def test_amap_client_reports_bounded_network_exhaustion(monkeypatch):
    monkeypatch.setenv("AMAP_MAX_RETRIES", "1")
    monkeypatch.setattr(
        "app.services.amap_client.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")),
    )
    monkeypatch.setattr("app.services.amap_client.time.sleep", lambda seconds: None)

    with pytest.raises(AppError) as caught:
        AmapClient(api_key="test-key").search_poi("武侯祠", city="成都")

    assert caught.value.code == "amap_network_error"
    assert caught.value.details["request_attempts"] == 2
