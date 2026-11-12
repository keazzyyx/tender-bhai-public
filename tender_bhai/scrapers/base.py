"""Shared scraper plumbing: polite async HTTP with retries, context, result types."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

from tender_bhai.config import Settings
from tender_bhai.models import Candidate, DetailResult

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class ScrapeContext:
    settings: Settings
    keywords: list[str] = field(default_factory=list)
    extra_keywords: list[str] = field(default_factory=list)
    ignore_keywords: list[str] = field(default_factory=list)
    max_pages: int = 5
    max_candidates: int = 60
    delay_seconds: float = 1.5
    proxy_url: str | None = None


@dataclass
class ScrapeResult:
    portal: str
    candidates: list[Candidate] = field(default_factory=list)
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | partial | failed | blocked

    def fail(self, message: str) -> None:
        self.errors.append(message[:500])
        self.status = "failed" if not self.candidates else "partial"


class PoliteClient:
    """httpx.AsyncClient with retries, jittered delay and a browser-like UA."""

    def __init__(
        self,
        delay_seconds: float = 1.5,
        verify: bool = False,
        timeout: float = 40.0,
        proxy_url: str | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> None:
        self.delay = delay_seconds
        self.retries = retries
        self._client = httpx.AsyncClient(
            headers={**DEFAULT_HEADERS, **(headers or {})},
            verify=verify,
            timeout=timeout,
            follow_redirects=True,
            proxy=proxy_url or None,
        )

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self._client.aclose()

    async def pause(self) -> None:
        await asyncio.sleep(self.delay + random.uniform(0, self.delay / 2))

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    raise httpx.HTTPStatusError(f"{response.status_code}", request=response.request, response=response)
                return response
            except (httpx.HTTPError, OSError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    backoff = min(2.0**attempt, 10.0) + random.uniform(0, 1)
                    log.info("retry %s %s in %.1fs (%s: %s)", method, url[:100], backoff, type(exc).__name__, exc)
                    await asyncio.sleep(backoff)
        raise RuntimeError(f"{method} {url} failed after {self.retries} attempts: {last_exc}")

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.text

    async def get_bytes(self, url: str, max_bytes: int = 15_000_000, **kwargs: Any) -> bytes:
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.content[:max_bytes]


class BaseScraper(ABC):
    """One portal. Subclasses implement ``scrape`` and optionally ``fetch_detail``."""

    name: str = "base"
    state: str = ""
    base_url: str = ""
    needs_browser: bool = False

    def __init__(self, ctx: ScrapeContext) -> None:
        self.ctx = ctx

    def client(self, **kwargs: Any) -> PoliteClient:
        return PoliteClient(
            delay_seconds=self.ctx.delay_seconds,
            verify=self.ctx.settings.verify_tls,
            proxy_url=self.ctx.proxy_url,
            **kwargs,
        )

    @abstractmethod
    async def scrape(self) -> ScrapeResult: ...

    async def fetch_detail(self, candidate: Candidate) -> DetailResult:
        """Default: fetch the detail page and return its visible text."""
        if not candidate.detail_url:
            return DetailResult()
        async with self.client() as client:
            response = await client.get(candidate.detail_url)
            if response.status_code != 200:
                return DetailResult(text="", fields={"status": response.status_code})
            content_type = response.headers.get("content-type", "")
            if "pdf" in content_type or response.content[:5] == b"%PDF-":
                return DetailResult(pdf_bytes=response.content, document_url=candidate.detail_url)
            return DetailResult(text=visible_text(response.text), fields={})

    def keyword_filter(self, candidates: Iterable[Candidate]) -> list[Candidate]:
        from tender_bhai.keywords import matches

        kept = []
        for candidate in candidates:
            blob = " ".join([candidate.title, candidate.listing_text])
            if matches(blob, self.ctx.extra_keywords, self.ctx.ignore_keywords):
                kept.append(candidate)
        return kept


_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


def visible_text(html: str, max_chars: int = 40000) -> str:
    """Readable text of an HTML page (scripts/styles dropped, whitespace collapsed)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "head"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = _WS.sub(" ", text)
    text = _NL.sub("\n", text)
    return text.strip()[:max_chars]


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def looks_like_captcha(html: str) -> bool:
    lowered = html.lower()
    return "captcha" in lowered and (
        "captchaimage" in lowered or "enter captcha" in lowered or "type the characters" in lowered
    )
