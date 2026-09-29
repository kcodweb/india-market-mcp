"""Tool calls recorded by record_fixtures.py and replayed by test_tools.py."""

from datetime import datetime

from india_market_mcp.format import IST

# The moment the fixtures were recorded. Tests pin the clock here so expiry and date logic replay exactly.
RECORDED_AT = datetime(2026, 9, 30, 4, 15, tzinfo=IST)

SCENARIOS: list[tuple[str, dict]] = [
    ("search_securities", {"query": "hdfc"}),
    ("search_securities", {"query": "nifty bank"}),
    ("get_quote", {"symbol": "RELIANCE"}),
    ("get_quote", {"symbol": "RELIANC"}),
    ("get_quote", {"symbol": "banknifty"}),
    ("get_price_history", {"symbol": "RELIANCE", "period": "1y"}),
    ("get_price_history", {"symbol": "NIFTY 50", "period": "5y"}),
    ("get_price_history", {"symbol": "NIFTY PVT BANK", "period": "6mo"}),
    ("get_market_overview", {}),
    ("get_index", {"index": "nifty 50", "limit": 10}),
    ("get_index", {"index": "not an index"}),
    ("get_option_chain", {"symbol": "NIFTY", "strikes_each_side": 5}),
    ("get_option_chain", {"symbol": "RELIANCE", "expiry": "monthly", "strikes_each_side": 3}),
    ("get_financials", {"symbol": "TCS", "period": "annual", "count": 4}),
    ("get_financials", {"symbol": "RELIANCE", "period": "quarterly", "count": 4}),
    ("get_shareholding", {"symbol": "RELIANCE"}),
    ("get_corporate_actions", {"symbol": "ITC"}),
    ("get_announcements", {"symbol": "RELIANCE", "days": 30}),
    ("search_mutual_funds", {"query": "parag parikh flexi"}),
    ("get_mutual_fund", {"scheme_code": 122639}),
]
