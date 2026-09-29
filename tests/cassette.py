"""Record real HTTP responses once, then replay them in tests so the suite is fast, offline and deterministic."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"

# Query parameters that change on every run and do not affect the response we care about.
VOLATILE_PARAMS = {"period2"}


def request_key(request: httpx.Request) -> str:
    params = sorted((k, v) for k, v in request.url.params.multi_items() if k not in VOLATILE_PARAMS)
    query = "&".join(f"{k}={v}" for k, v in params)
    return f"{request.url.host}{request.url.path}" + (f"?{query}" if query else "")


def fixture_path(key: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")[:90]
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return FIXTURES / f"{slug}_{digest}.json"


class RecordingTransport(httpx.AsyncBaseTransport):
    """Passes requests through to the network and saves each response under tests/fixtures."""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.saved: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        content_type = response.headers.get("content-type", "")
        text = body.decode("utf-8", errors="replace")
        if "json" not in content_type:
            text = text[:300]  # HTML pages (NSE's cookie warm-up) are not needed in full
        key = request_key(request)
        FIXTURES.mkdir(exist_ok=True)
        record = {"key": key, "status": response.status_code, "content_type": content_type, "body": text}
        fixture_path(key).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        self.saved.append(key)
        return httpx.Response(response.status_code, headers={"content-type": content_type}, content=body)

    async def aclose(self) -> None:
        await self.inner.aclose()


class MissingFixtureError(Exception):
    pass


class ReplayTransport(httpx.AsyncBaseTransport):
    """Serves recorded responses. An unrecorded request is a test bug, so it fails loudly."""

    def __init__(self, overrides: dict[str, httpx.Response] | None = None) -> None:
        self.overrides = overrides or {}
        self.requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        self.requests.append(key)
        if key in self.overrides:
            return self.overrides[key]
        path = fixture_path(key)
        if not path.exists():
            raise MissingFixtureError(f"No recorded response for {key} (run tests/record_fixtures.py)")
        record = json.loads(path.read_text(encoding="utf-8"))
        return httpx.Response(
            record["status"],
            headers={"content-type": record["content_type"] or "application/json"},
            content=record["body"].encode("utf-8"),
        )
