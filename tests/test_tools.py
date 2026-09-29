"""End-to-end: real MCP client -> server -> features -> recorded NSE/Yahoo/mfapi responses."""

import pytest
from mcp import Client
from scenarios import SCENARIOS

from india_market_mcp import server

EXPECTED_TOOLS = {
    "search_securities",
    "get_quote",
    "get_price_history",
    "get_market_overview",
    "get_index",
    "get_option_chain",
    "price_option",
    "get_financials",
    "get_shareholding",
    "get_corporate_actions",
    "get_announcements",
    "search_mutual_funds",
    "get_mutual_fund",
}


async def test_tools_are_described_and_read_only(replay):
    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
        prompts = (await client.list_prompts()).prompts
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.description and len(t.description) > 40, t.name
        assert t.annotations and t.annotations.read_only_hint, t.name
        assert "ctx" not in t.input_schema.get("properties", {}), t.name
        for name, prop in t.input_schema.get("properties", {}).items():
            assert prop.get("description") or prop.get("anyOf"), f"{t.name}.{name} has no description"
    assert {p.name for p in prompts} == {"research_stock", "market_brief", "options_positioning"}


@pytest.mark.parametrize("name,args", SCENARIOS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(SCENARIOS)])
async def test_every_recorded_scenario_replays(call, name, args):
    out = await call(name, args)
    expected_error = args.get("symbol") == "RELIANC" or args.get("index") == "not an index"
    assert ("error" in out) == expected_error, out


async def test_quote(call):
    q = await call("get_quote", {"symbol": "reliance"})
    assert q["symbol"] == "RELIANCE" and q["type"] == "equity"
    assert q["price"] == 1182.0 and q["change_pct"] == -1.3
    assert q["market_cap_cr"] == pytest.approx(1599546.08)
    assert q["week52"]["low_date"] == "2026-09-29"
    assert q["pe"] == 18.38 and q["has_derivatives"] is True
    assert "NIFTY 50" in q["key_indices"]


async def test_unknown_symbol_suggests_alternatives(call):
    out = await call("get_quote", {"symbol": "RELIANC"})
    assert "Did you mean" in out["error"] and "RELIANCE (Reliance Industries Limited)" in out["error"]


async def test_index_name_in_get_quote_returns_the_index(call):
    q = await call("get_quote", {"symbol": "banknifty"})
    assert q["type"] == "index" and q["index"] == "NIFTY BANK"
    assert q["valuation"]["pe"] > 0


async def test_search(call):
    out = await call("search_securities", {"query": "hdfc"})
    symbols = [s["symbol"] for s in out["securities"]]
    assert "HDFCBANK" in symbols and "HDFCLIFE" in symbols
    out = await call("search_securities", {"query": "nifty bank"})
    assert any(i["code"] == "NIFTY BANK" for i in out["indices"])


async def test_price_history_stats(call):
    h = await call("get_price_history", {"symbol": "RELIANCE", "period": "1y"})
    s = h["stats"]
    assert h["yahoo_ticker"] == "RELIANCE.NS"
    assert s["end_date"] == "2026-09-29" and s["last_close"] == 1182.0  # latest bar filled from Yahoo's meta
    assert s["max_drawdown_pct"] < 0 < s["annualized_volatility_pct"]
    assert h["trend"]["sma_200"] and 0 <= h["trend"]["rsi_14"] <= 100
    assert h["bar_interval"] == "1wk" and 50 <= len(h["bars"]) <= 54


async def test_long_index_history_uses_weekly_data(call):
    h = await call("get_price_history", {"symbol": "NIFTY 50", "period": "5y"})
    assert h["yahoo_ticker"] == "^NSEI" and h["bar_interval"] == "1mo"
    assert h["stats"]["cagr_pct"] is not None and "trend" not in h


async def test_market_overview(call):
    o = await call("get_market_overview")
    assert o["market"]["equities"] == "Closed"
    names = [i["index"] for i in o["indices"]]
    assert names[0] == "NIFTY 50" and "INDIA VIX" in names
    assert o["nifty50_valuation"]["pe"] == 19.2
    changes = [s["change_pct"] for s in o["sectors_by_change"]]
    assert changes == sorted(changes, reverse=True)
    flows = {f["category"]: f["net"] for f in o["institutional_flows_cr"]}
    assert flows == {"DII": 6952.71, "FII/FPI": -9980.22}
    assert len(o["nifty50_top_gainers"]) == 5
    assert "unavailable" not in o


async def test_index_constituents(call):
    idx = await call("get_index", {"index": "nifty 50", "limit": 10})
    assert idx["code"] == "NIFTY 50" and idx["constituent_count"] == 50
    assert len(idx["constituents"]) == 10 and "note" in idx
    weights = [c["weight_pct_approx"] for c in idx["constituents"]]
    assert weights == sorted(weights, reverse=True)
    assert idx["breadth"]["advances"] + idx["breadth"]["declines"] + idx["breadth"]["unchanged"] == 50


async def test_unknown_index(call):
    out = await call("get_index", {"index": "not an index"})
    assert "No NSE index matches" in out["error"]


async def test_nifty_option_chain_analytics(call):
    oc = await call("get_option_chain", {"symbol": "NIFTY", "strikes_each_side": 5})
    s = oc["summary"]
    assert oc["expiry"] == "2026-10-06" and oc["atm_strike"] == 22700
    assert oc["underlying_price"] == 22716.2
    assert len(oc["chain"]) == 11
    assert s["pcr_oi"] == pytest.approx(s["total_put_oi"] / s["total_call_oi"], abs=1e-3)
    assert s["max_pain"] % 50 == 0
    assert s["atm_straddle"] == pytest.approx(219.5 + 107.0)
    assert s["expected_move"]["range"][0] < 22716.2 < s["expected_move"]["range"][1]
    atm = next(r for r in oc["chain"] if r["strike"] == 22700)
    assert 0 < atm["call"]["delta"] < 1 and -1 < atm["put"]["delta"] < 0
    assert "2026-09-29" not in oc["expiries"]


async def test_stock_option_chain_monthly(call):
    oc = await call("get_option_chain", {"symbol": "RELIANCE", "expiry": "monthly", "strikes_each_side": 3})
    assert oc["underlying_type"] == "stock" and oc["expiry"] == "2026-10-27"
    assert oc["atm_strike"] == 1180


async def test_annual_financials(call):
    f = await call("get_financials", {"symbol": "TCS", "period": "annual", "count": 4})
    latest = f["periods"][0]
    assert latest["period_end"] == "2026-03-31"
    assert latest["revenue_cr"] == pytest.approx(267021.0)
    assert latest["margins_pct"]["net"] == pytest.approx(100 * 49210 / 267021, abs=0.01)
    assert latest["growth_yoy_pct"]["revenue"] == pytest.approx(4.58, abs=0.01)
    assert latest["ratios"]["roe_pct"] > 30
    assert "freshness_warning" not in f


async def test_quarterly_financials(call):
    f = await call("get_financials", {"symbol": "RELIANCE", "period": "quarterly", "count": 4})
    ends = [p["period_end"] for p in f["periods"]]
    assert ends == sorted(ends, reverse=True) and ends[0] == "2026-06-30"


async def test_shareholding(call):
    sh = await call("get_shareholding", {"symbol": "RELIANCE"})
    assert sh["quarters"][0] == {
        "quarter_end": "2026-06-30",
        "promoter_pct": 50.48,
        "public_pct": 49.52,
        "filed": "2026-07-16",
    }
    assert sh["promoter_change_pts"]["qoq"] == pytest.approx(0.48)


async def test_corporate_actions_with_trailing_yield(call):
    ca = await call("get_corporate_actions", {"symbol": "ITC"})
    first = ca["actions"][0]
    assert first["type"] == "dividend" and first["dividend_per_share"] == 8.0
    assert ca["dividends_last_12m_per_share"] == 14.5
    assert ca["trailing_dividend_yield_pct"] == pytest.approx(100 * 14.5 / ca["price_used"], abs=0.01)


async def test_announcements(call):
    an = await call("get_announcements", {"symbol": "RELIANCE", "days": 30})
    assert an["from"] == "2026-08-31" and an["to"] == "2026-09-30"
    assert an["count"] >= len(an["announcements"]) > 0
    assert all(a["document"].startswith("https://") for a in an["announcements"] if "document" in a)


async def test_mutual_funds(call):
    found = await call("search_mutual_funds", {"query": "parag parikh flexi"})
    assert found["schemes"][0] == {
        "scheme_code": 122639,
        "name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
        "plan": "direct",
        "option": "growth",
    }
    mf = await call("get_mutual_fund", {"scheme_code": 122639})
    assert mf["fund_house"] == "PPFAS Mutual Fund" and mf["nav"]["value"] > 0
    assert {"1y", "3y_cagr", "5y_cagr", "10y_cagr"} <= set(mf["returns_pct"])
    assert mf["risk"]["max_drawdown_all_time"]["max_drawdown_pct"] < 0


async def test_price_option_through_mcp(call):
    out = await call(
        "price_option",
        {"spot": 100, "strike": 100, "days_to_expiry": 365, "option_type": "call", "volatility_pct": 20, "rate_pct": 0},
    )
    assert out["price"] == pytest.approx(7.9656, abs=1e-3)


async def test_input_validation_happens_before_any_request(call, replay):
    out = await call("get_option_chain", {"symbol": "NIFTY", "strikes_each_side": 500})
    assert "error" in out
    assert replay.requests == []


async def test_index_history_falls_back_to_nse_closes(call):
    h = await call("get_price_history", {"symbol": "NIFTY PVT BANK", "period": "6mo"})
    assert h["source"] == "NSE index closes" and "yahoo_ticker" not in h
    assert h["stats"]["last_close"] == 26443.15 and h["stats"]["end_date"] == "2026-09-29"
    assert h["trend"]["sma_200"] is not None
    assert all(set(b) == {"date", "close"} for b in h["bars"])


async def test_concurrent_requests_share_one_nse_session(call, replay):
    await call("get_market_overview")
    assert replay.requests.count("www.nseindia.com/") == 1
