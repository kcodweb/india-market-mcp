"""Shared HTTP plumbing: a TTL cache, polite rate limiting and retries."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .errors import SourceUnavailableError

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

RETRY_STATUS = {429, 500, 502, 503, 504}


class TTLCache:
    """A small in-memory cache. Entries expire after their own TTL."""

    def __init__(self, max_entries: int = 512) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._max = max_entries

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._data) >= self._max:
            # Drop the entry closest to expiry. Cheap enough at this size.
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + ttl, value)


class RateLimiter:
    """Keeps at least `interval` seconds between requests to one host."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


async def get_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    limiter: RateLimiter | None = None,
    attempts: int = 3,
) -> httpx.Response:
    """GET with retries on network errors and 429/5xx. Other statuses are returned to the caller."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        if limiter:
            await limiter.wait()
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            last_error = exc
            log.warning("%s request failed (%s), attempt %d", source, exc, attempt + 1)
        else:
            if resp.status_code not in RETRY_STATUS:
                return resp
            last_error = SourceUnavailableError(f"{source} returned HTTP {resp.status_code}")
            log.warning("%s returned %d for %s, attempt %d", source, resp.status_code, url, attempt + 1)
        if attempt + 1 < attempts:
            await asyncio.sleep(0.6 * (2**attempt))
    raise SourceUnavailableError(
        f"{source} is not responding right now ({last_error}). Try again in a minute."
    ) from last_error


def parse_json(resp: httpx.Response, source: str) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        snippet = resp.text[:120].replace("\n", " ")
        raise SourceUnavailableError(
            f"{source} returned something that is not JSON (HTTP {resp.status_code}): {snippet!r}"
        ) from exc
