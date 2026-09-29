from datetime import date, timedelta

import pytest

from india_market_mcp.analytics import returns as R


def daily(values, start=date(2020, 1, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_total_return_and_cagr():
    assert R.total_return_pct(daily([100, 110, 121])) == pytest.approx(21.0)
    assert R.cagr_pct(100, 121, 2) == pytest.approx(10.0)
    assert R.cagr_pct(0, 121, 2) is None


def test_max_drawdown_reports_peak_and_trough_dates():
    s = daily([100, 120, 90, 110, 60, 130])
    dd = R.max_drawdown(s)
    assert dd["max_drawdown_pct"] == pytest.approx(-50.0)
    assert dd["peak_date"] == s[1][0].isoformat()
    assert dd["trough_date"] == s[4][0].isoformat()


def test_annualized_vol_of_constant_growth_is_zero():
    s = daily([100 * 1.01**i for i in range(30)])
    assert R.annualized_vol_pct(s, 252) == pytest.approx(0.0, abs=1e-9)


def test_trailing_returns_skip_windows_longer_than_the_history():
    # Two years of steady 10%-a-year growth.
    start = date(2024, 1, 1)
    s = [(start + timedelta(days=i), 100 * 1.10 ** (i / 365.25)) for i in range(0, 731)]
    out = R.trailing_returns(s)
    assert out["1y"] == pytest.approx(10.0, abs=0.05)
    assert "3y_cagr" not in out and "5y_cagr" not in out
    assert out["since_inception_cagr"] == pytest.approx(10.0, abs=0.05)


def test_calendar_year_returns_label_the_current_year_ytd():
    s = [(date(2023, 12, 29), 100.0), (date(2024, 12, 31), 120.0), (date(2025, 6, 30), 90.0)]
    assert R.calendar_year_returns(s) == {"2024": 20.0, "2025_ytd": -25.0}


def test_value_on_or_before():
    s = daily([1, 2, 3, 4])
    assert R.value_on_or_before(s, date(2020, 1, 3)) == (date(2020, 1, 3), 3)
    assert R.value_on_or_before(s, date(2019, 12, 31)) is None
    assert R.value_on_or_before(s, date(2021, 1, 1)) == (date(2020, 1, 4), 4)


def test_sma_and_rsi():
    assert R.sma([1, 2, 3, 4], 2) == 3.5
    assert R.sma([1], 2) is None
    assert R.rsi(list(range(1, 40))) == 100.0
    assert R.rsi(list(range(40, 1, -1))) == pytest.approx(0.0)
    assert R.rsi([1, 2]) is None
