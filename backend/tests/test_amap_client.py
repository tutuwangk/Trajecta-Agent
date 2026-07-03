from app.services.amap_client import AmapClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

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
