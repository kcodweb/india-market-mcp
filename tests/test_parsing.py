from datetime import date, datetime, timedelta

import pytest

from india_market_mcp.errors import InvalidInputError, NotFoundError
from india_market_mcp.features.company import classify_action
from india_market_mcp.features.derivatives import live_expiries, pick_expiry
from india_market_mcp.features.funds import classify_scheme
from india_market_mcp.features.history import resample
from india_market_mcp.format import IST, compact, crore, num, parse_date
from india_market_mcp.sources.yahoo import chart_rows
from india_market_mcp.symbols import normalize_equity_symbol, option_symbol_for, yahoo_index_ticker


@pytest.mark.parametrize(
    "raw,expected",
    [("reliance", "RELIANCE"), (" NSE:tcs ", "TCS"), ("INFY.NS", "INFY"), ("m&m", "M&M"), ("bajaj-auto", "BAJAJ-AUTO")],
)
def test_normalize_equity_symbol(raw, expected):
    assert normalize_equity_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "RELIANCE.BO", "reliance industries ltd", "<script>"])
def test_normalize_equity_symbol_rejects(raw):
    with pytest.raises(InvalidInputError):
        normalize_equity_symbol(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NIFTY", "NIFTY"),
        ("nifty 50", "NIFTY"),
        ("Bank Nifty", "BANKNIFTY"),
        ("NIFTY BANK", "BANKNIFTY"),
        ("finnifty", "FINNIFTY"),
        ("NIFTY FIN SERVICE", "FINNIFTY"),
        ("RELIANCE", None),
    ],
)
def test_option_symbol_for(raw, expected):
    assert option_symbol_for(raw) == expected


def test_yahoo_index_tickers():
    assert yahoo_index_ticker("NIFTY 50") == "^NSEI"
    assert yahoo_index_ticker("NIFTY PVT BANK") == "NIFTY_PVT_BANK.NS"
    assert yahoo_index_ticker("NIFTY OIL AND GAS") == "NIFTY_OIL_AND_GAS.NS"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("29-Sep-2026", date(2026, 9, 29)),
        ("16-JUL-2026 19:24:44", date(2026, 7, 16)),
        ("29-Sep-2026 15:30", date(2026, 9, 29)),
        ("28-09-2026", date(2026, 9, 28)),
        ("2026-09-29", date(2026, 9, 29)),
        ("-", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_number_helpers():
    assert num("1,234.5") == 1234.5
    assert num("-") is None and num(None) is None and num(True) is None and num(float("nan")) is None
    assert crore(15995460769404) == pytest.approx(1599546.08)
    assert compact({"a": None, "b": {}, "c": [], "d": {"e": None, "f": 0}, "g": [1, None]}) == {"d": {"f": 0}, "g": [1]}


@pytest.mark.parametrize(
    "subject,kind,amount",
    [
        ("Dividend - Rs 6 Per Share", "dividend", 6.0),
        ("Interim Dividend - Rs 6.50 Per Share", "dividend", 6.5),
        ("Final Dividend - Rs 10 Per Share And Special Dividend - Rs 5 Per Share", "dividend", 15.0),
        ("Annual General Meeting/Dividend - Re 1 Per Share", "dividend", 1.0),
        ("Bonus 1:1", "bonus", None),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 1/- Per Share", "split", None),
        ("Buy Back", "buyback", None),
        ("Demerger", "demerger", None),
        ("Annual General Meeting", "meeting", None),
        ("Something new", "other", None),
    ],
)
def test_classify_action(subject, kind, amount):
    assert classify_action(subject) == (kind, amount)


@pytest.mark.parametrize(
    "name,plan,option",
    [
        ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "direct", "growth"),
        ("Parag Parikh Flexi Cap Fund - Regular Plan - Growth", "regular", "growth"),
        ("HDFC Mid-Cap Fund - Direct Plan - IDCW Payout", "direct", "idcw"),
    ],
)
def test_classify_scheme(name, plan, option):
    assert classify_scheme(name) == {"plan": plan, "option": option}


def test_expiry_selection():
    now = datetime(2026, 9, 30, 8, 0, tzinfo=IST)
    raw = ["29-Sep-2026", "06-Oct-2026", "13-Oct-2026", "27-Oct-2026", "03-Nov-2026", "24-Nov-2026"]
    live = live_expiries(raw, now)
    assert live[0] == date(2026, 10, 6)  # 29 Sep has expired
    assert pick_expiry(live, None) == date(2026, 10, 6)
    assert pick_expiry(live, "next") == date(2026, 10, 13)
    assert pick_expiry(live, "monthly") == date(2026, 10, 27)
    assert pick_expiry(live, "2026-11-03") == date(2026, 11, 3)
    assert pick_expiry(live, "27 Oct") == date(2026, 10, 27)
    assert pick_expiry(live, "27-Oct-2026") == date(2026, 10, 27)
    with pytest.raises(NotFoundError):
        pick_expiry(live, "2026-10-08")
    with pytest.raises(InvalidInputError):
        pick_expiry(live, "soon")
    # On expiry day the contract is live until 15:30 IST.
    assert live_expiries(["30-Sep-2026"], now) == [date(2026, 9, 30)]
    assert live_expiries(["30-Sep-2026"], datetime(2026, 9, 30, 16, 0, tzinfo=IST)) == []


def test_chart_rows_fill_the_empty_latest_bar_from_meta():
    result = {
        "meta": {"regularMarketPrice": 105.0, "regularMarketTime": 1790675106, "regularMarketDayHigh": 106.0},
        "timestamp": [1790567100, 1790653500],  # 28 and 29 Sep 2026, 09:15 IST
        "indicators": {
            "quote": [
                {
                    "open": [100, 104],
                    "high": [101, None],
                    "low": [99, None],
                    "close": [100.5, None],
                    "volume": [5, None],
                }
            ],
            "adjclose": [{"adjclose": [100.4, None]}],
        },
    }
    rows = chart_rows(result)
    assert [r["date"] for r in rows] == [date(2026, 9, 28), date(2026, 9, 29)]
    assert rows[-1]["close"] == 105.0 and rows[-1]["high"] == 106.0 and rows[-1]["open"] == 104


def test_resample_to_weeks():
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(10)]
    rows = [
        {"date": d, "open": i, "high": i + 1, "low": i - 1, "close": i + 0.5, "adj_close": i + 0.5, "volume": 10}
        for i, d in enumerate(days)
    ]
    weeks = resample(rows, "1wk")
    assert len(weeks) == 2
    assert weeks[0]["open"] == 0 and weeks[0]["close"] == 6.5 and weeks[0]["high"] == 7 and weeks[0]["volume"] == 70
