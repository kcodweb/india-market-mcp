"""Mutual fund NAVs from mfapi.in, a free API over AMFI's daily NAV files."""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import NotFoundError, SourceUnavailableError
from ..http import BROWSER_UA, RateLimiter, TTLCache, get_with_retries, parse_json

BASE = "https://api.mfapi.in"

TTL_SEARCH = 24 * 3600
TTL_NAV = 3600


class MfApiClient:
    def __init__(self, http: httpx.AsyncClient, cache: TTLCache) -> None:
        self.http = http
        self.cache = cache
        self.limiter = RateLimiter(0.2)
        self.headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any] | None, ttl: float) -> Any:
        key = "mfapi:" + path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        resp = await get_with_retries(
            self.http,
            BASE + path,
            source="mfapi.in",
            params=params,
            headers=self.headers,
            limiter=self.limiter,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise SourceUnavailableError(f"mfapi.in returned HTTP {resp.status_code}. Try again later.")
        data = parse_json(resp, "mfapi.in")
        self.cache.set(key, data, ttl)
        return data

    async def search(self, query: str) -> list[dict]:
        data = await self._get("/mf/search", {"q": query}, TTL_SEARCH)
        return data if isinstance(data, list) else []

    async def scheme(self, code: int) -> dict:
        """Scheme metadata plus its full NAV history (newest first, as mfapi returns it)."""
        data = await self._get(f"/mf/{code}", None, TTL_NAV)
        if not data or not data.get("meta") or not data.get("data") or data.get("status") == "FAIL":
            raise NotFoundError(
                f"No mutual fund scheme with code {code}. Use search_mutual_funds to find the right code."
            )
        return data
