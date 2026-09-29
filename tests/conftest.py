from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from cassette import ReplayTransport  # noqa: E402
from mcp import Client  # noqa: E402
from scenarios import RECORDED_AT  # noqa: E402

from india_market_mcp import format as fmt  # noqa: E402
from india_market_mcp import server  # noqa: E402


@pytest.fixture(autouse=True)
def frozen_clock():
    fmt.frozen_now = RECORDED_AT
    yield RECORDED_AT
    fmt.frozen_now = None


@pytest.fixture
def replay():
    transport = ReplayTransport()
    server.service_options.clear()
    server.service_options.update(transport=transport, nse_min_interval=0.0)
    yield transport
    server.service_options.clear()


class ToolCaller:
    """Calls tools on the real server through an in-process MCP client."""

    async def __call__(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        async with Client(server.mcp) as client:
            result = await client.call_tool(name, args or {})
        text = result.content[0].text if result.content else ""
        if result.is_error:
            return {"error": text}
        data = json.loads(text)
        assert data == result.structured_content, "text and structured content should match"
        return data


@pytest.fixture
def call(replay) -> ToolCaller:
    return ToolCaller()
