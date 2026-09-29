"""Checks against the real sites, to catch NSE/Yahoo API changes. Skipped by default; run with `pytest -m live`.

NSE blocks many cloud and non-Indian networks, so run these from an ordinary Indian connection.
"""

import pytest
from mcp import Client

from india_market_mcp import format as fmt
from india_market_mcp import server

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def real_clock_and_network():
    fmt.frozen_now = None
    server.service_options.clear()
    yield


async def _call(name, args=None):
    async with Client(server.mcp) as client:
        result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_live_quote():
    q = await _call("get_quote", {"symbol": "RELIANCE"})
    assert q["price"] > 0 and q["market_cap_cr"] > 0


async def test_live_overview():
    o = await _call("get_market_overview")
    assert "unavailable" not in o, o.get("unavailable")


async def test_live_option_chain():
    oc = await _call("get_option_chain", {"symbol": "NIFTY", "strikes_each_side": 2})
    assert oc["summary"]["total_call_oi"] > 0


async def test_live_price_history_and_financials():
    h = await _call("get_price_history", {"symbol": "TCS", "period": "6mo"})
    assert h["stats"]["last_close"] > 0
    f = await _call("get_financials", {"symbol": "TCS"})
    assert f["periods"][0]["revenue_cr"] > 0


async def test_live_mutual_fund():
    mf = await _call("get_mutual_fund", {"scheme_code": 122639})
    assert mf["nav"]["value"] > 0
