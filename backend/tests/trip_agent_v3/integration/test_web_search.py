from contextlib import contextmanager

import httpx

from app.trip_agent_v3.adapters.web_search import WebSearchClient, WebSearchResult


def test_page_lookup_keeps_place_identity_and_operating_section(monkeypatch):
    html = "<html><title>测试购物中心</title><script>invented hours</script><style>.fake{}</style><body>" + "宣传内容 " * 2000 + "<p>商场营业时间：每日10:00-22:00。</p></body></html>"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}))

    @contextmanager
    def stream(*args, **kwargs):
        with httpx.Client(transport=transport) as client:
            with client.stream(*args, **kwargs) as response:
                yield response

    monkeypatch.setattr(httpx, "stream", stream)
    result = WebSearchClient().fetch(WebSearchResult(title="官网", url="https://example.test/mall"))
    assert "测试购物中心" in result.snippet
    assert "每日10:00-22:00" in result.snippet
    assert "invented hours" not in result.snippet
    assert len(result.snippet) <= 4000
