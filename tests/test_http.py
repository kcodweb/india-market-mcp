import asyncio

import httpx
import pytest

from india_market_mcp.errors import SourceUnavailableError
from india_market_mcp.http import TTLCache
from india_market_mcp.sources.nse import NseClient


class Scripted(httpx.AsyncBaseTransport):
    """Returns queued responses per path and records every request path."""

    def __init__(self, script: dict[str, list[httpx.Response]]):
        self.script = script
        self.paths: list[str] = []

    async def handle_async_request(self, request):
        self.paths.append(request.url.path)
        queue = self.script[request.url.path]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def ok(data):
    return httpx.Response(200, json=data)


async def test_nse_refreshes_cookies_once_after_a_403():
    transport = Scripted(
        {
            "/": [httpx.Response(200, text="<html></html>")],
            "/api/marketStatus": [httpx.Response(403, text="Access Denied"), ok({"marketState": []})],
        }
    )
    async with httpx.AsyncClient(transport=transport) as http:
        nse = NseClient(http, TTLCache(), min_interval=0)
        assert await nse.market_status() == {"marketState": []}
    assert transport.paths == ["/", "/api/marketStatus", "/", "/api/marketStatus"]


async def test_nse_retries_server_errors_then_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr("india_market_mcp.http.asyncio.sleep", _no_sleep)
    transport = Scripted({"/": [httpx.Response(200, text="ok")], "/api/marketStatus": [httpx.Response(503)]})
    async with httpx.AsyncClient(transport=transport) as http:
        nse = NseClient(http, TTLCache(), min_interval=0)
        with pytest.raises(SourceUnavailableError, match="not responding"):
            await nse.market_status()
    assert transport.paths.count("/api/marketStatus") == 3


async def test_nse_blocked_session_explains_itself():
    transport = Scripted({"/": [httpx.Response(403, text="Access Denied")]})
    async with httpx.AsyncClient(transport=transport) as http:
        nse = NseClient(http, TTLCache(), min_interval=0)
        with pytest.raises(SourceUnavailableError, match="blocks some networks"):
            await nse.market_status()


async def test_nse_responses_are_cached():
    transport = Scripted({"/": [httpx.Response(200, text="ok")], "/api/marketStatus": [ok({"n": 1})]})
    async with httpx.AsyncClient(transport=transport) as http:
        nse = NseClient(http, TTLCache(), min_interval=0)
        await nse.market_status()
        await nse.market_status()
    assert transport.paths.count("/api/marketStatus") == 1


def test_ttl_cache_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("india_market_mcp.http.time.monotonic", lambda: clock[0])
    cache = TTLCache(max_entries=2)
    cache.set("a", 1, ttl=10)
    assert cache.get("a") == 1
    clock[0] += 11
    assert cache.get("a") is None
    cache.set("a", 1, ttl=10)
    cache.set("b", 2, ttl=20)
    cache.set("c", 3, ttl=30)  # evicts the entry closest to expiry
    assert cache.get("a") is None and cache.get("b") == 2 and cache.get("c") == 3


async def _no_sleep(_seconds):
    return None


async def test_a_rejected_session_is_refreshed_only_once():
    transport = Scripted({"/": [httpx.Response(200, text="ok")]})
    async with httpx.AsyncClient(transport=transport) as http:
        nse = NseClient(http, TTLCache(), min_interval=0)
        await nse._ensure_session()
        rejected = nse._session_gen
        # Two requests refused with the same session both ask for a refresh; only the first one does it.
        await asyncio.gather(nse._ensure_session(rejected_gen=rejected), nse._ensure_session(rejected_gen=rejected))
        await nse._ensure_session()  # a fresh session is reused
    assert transport.paths.count("/") == 2
