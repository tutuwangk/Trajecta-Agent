from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import os
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import Field

from app.core import AppError
from app.trip_agent_v3.domain.common import DomainModel


class WebSearchResult(DomainModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    snippet: str = ""


class WebSearchClient:
    """Bounded public-search boundary; page HTML never enters the Agent."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "POI_WEB_SEARCH_URL",
            "https://html.duckduckgo.com/html/",
        )
        self.timeout_seconds = timeout_seconds or _positive_float_env(
            "POI_WEB_SEARCH_TIMEOUT_SECONDS",
            12.0,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[WebSearchResult]:
        try:
            response = httpx.get(
                self.base_url,
                params={"q": query},
                headers={
                    "User-Agent": (
                        "Trajecta-Agent/3.0 "
                        "(+bounded itinerary fact resolver)"
                    )
                },
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AppError(
                "POI 网络事实搜索暂时不可用，运行将保留具体事实缺口。",
                code="poi_web_search_failed",
                step="resolve_poi_facts",
            ) from exc
        parser = _DuckDuckGoParser(max_results=max_results)
        parser.feed(response.text)
        return parser.results


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, *, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max_results
        self.results: list[WebSearchResult] = []
        self._anchor_url = ""
        self._anchor_text: list[str] = []
        self._snippet_text: list[str] = []
        self._in_result_anchor = False
        self._in_snippet = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if (
            tag == "a"
            and "result__a" in classes
            and len(self.results) < self.max_results
        ):
            self._in_result_anchor = True
            self._anchor_url = _direct_url(
                str(values.get("href") or "")
            )
            self._anchor_text = []
        elif "result__snippet" in classes and self.results:
            self._in_snippet = True
            self._snippet_text = []

    def handle_data(self, data: str) -> None:
        if self._in_result_anchor:
            self._anchor_text.append(data)
        elif self._in_snippet:
            self._snippet_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_anchor:
            self._in_result_anchor = False
            title = _clean_text(" ".join(self._anchor_text))
            if title and self._anchor_url:
                self.results.append(
                    WebSearchResult(
                        title=title,
                        url=self._anchor_url,
                    )
                )
        elif self._in_snippet and tag in {"a", "div", "span"}:
            self._in_snippet = False
            snippet = _clean_text(" ".join(self._snippet_text))
            if snippet and self.results:
                self.results[-1] = self.results[-1].model_copy(
                    update={"snippet": snippet}
                )


def _direct_url(value: str) -> str:
    parsed = urlparse(unescape(value))
    redirect = parse_qs(parsed.query).get("uddg")
    return unquote(redirect[0]) if redirect else value


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default
