"""Company fundamentals, ownership, corporate actions and filings."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ..errors import InvalidInputError, MarketDataError, NotFoundError
from ..format import crore, iso, parse_date, pct, rnd, today_ist
from ..services import Services
from ..symbols import normalize_equity_symbol
from .market import get_quote, not_found_with_suggestions

# --- Financial statements ----------------------------------------------------------------------

INCOME = [
    "TotalRevenue",
    "OperatingRevenue",
    "GrossProfit",
    "EBITDA",
    "OperatingIncome",
    "PretaxIncome",
    "InterestExpense",
    "NetIncomeCommonStockholders",
    "NetIncome",
    "DilutedEPS",
]
BALANCE = [
    "TotalAssets",
    "StockholdersEquity",
    "TotalDebt",
    "CashAndCashEquivalents",
    "CurrentAssets",
    "CurrentLiabilities",
]
CASHFLOW = ["OperatingCashFlow", "CapitalExpenditure", "FreeCashFlow", "CashDividendsPaid"]


async def get_financials(svc: Services, symbol: str, period: str = "annual", count: int = 5) -> dict[str, Any]:
    period = period.strip().lower()
    if period not in ("annual", "quarterly"):
        raise InvalidInputError("period must be 'annual' or 'quarterly'.")
    if not 1 <= count <= 12:
        raise InvalidInputError("count must be between 1 and 12.")
    sym = normalize_equity_symbol(symbol)
    names = INCOME + BALANCE + CASHFLOW
    data = await svc.yahoo.timeseries(sym + ".NS", [period + n for n in names])
    table: dict[str, dict[str, float]] = {}
    for full, points in data.items():
        field = full[len(period) :]
        for p in points:
            table.setdefault(p["date"], {})[field] = p["value"]
    dates = sorted(d for d, v in table.items() if "TotalRevenue" in v or "NetIncome" in v)
    if not dates:
        try:
            await svc.nse.meta(sym)
        except NotFoundError:
            raise await not_found_with_suggestions(svc, sym) from None
        raise NotFoundError(
            f"No {period} financial statements are available for {sym} (common for recent listings, "
            "some banks and non-equity securities)."
        )

    def val(d: str, field: str) -> float | None:
        return table.get(d, {}).get(field)

    def prior(d: str) -> str | None:
        """Same period a year earlier (previous year for annual data)."""
        target = date.fromisoformat(d) - timedelta(days=365)
        close = [x for x in dates if abs((date.fromisoformat(x) - target).days) <= 20]
        return close[0] if close else None

    periods = []
    for d in dates[-count:]:
        revenue = val(d, "TotalRevenue") or val(d, "OperatingRevenue")
        net = val(d, "NetIncomeCommonStockholders") or val(d, "NetIncome")
        ebitda = val(d, "EBITDA")
        op = val(d, "OperatingIncome")
        equity = val(d, "StockholdersEquity")
        debt = val(d, "TotalDebt")
        p = prior(d)
        prev_rev = (val(p, "TotalRevenue") or val(p, "OperatingRevenue")) if p else None
        prev_net = (val(p, "NetIncomeCommonStockholders") or val(p, "NetIncome")) if p else None
        prev_eps = val(p, "DilutedEPS") if p else None
        eps = val(d, "DilutedEPS")
        entry = {
            "period_end": d,
            "revenue_cr": crore(revenue),
            "ebitda_cr": crore(ebitda),
            "operating_income_cr": crore(op),
            "net_income_cr": crore(net),
            "eps_diluted": rnd(eps),
            "margins_pct": {
                "ebitda": pct(ebitda, revenue),
                "operating": pct(op, revenue),
                "net": pct(net, revenue),
            },
            "growth_yoy_pct": {
                "revenue": _growth(revenue, prev_rev),
                "net_income": _growth(net, prev_net),
                "eps": _growth(eps, prev_eps),
            },
            "balance_sheet_cr": {
                "total_assets": crore(val(d, "TotalAssets")),
                "equity": crore(equity),
                "total_debt": crore(debt),
                "cash": crore(val(d, "CashAndCashEquivalents")),
            },
            "cash_flow_cr": {
                "operating": crore(val(d, "OperatingCashFlow")),
                "capex": crore(val(d, "CapitalExpenditure")),
                "free_cash_flow": crore(val(d, "FreeCashFlow")),
                "dividends_paid": crore(val(d, "CashDividendsPaid")),
            },
            "ratios": {
                "roe_pct": pct(net, equity) if period == "annual" else None,
                "debt_to_equity": rnd(debt / equity) if debt is not None and equity else None,
                "current_ratio": _ratio(val(d, "CurrentAssets"), val(d, "CurrentLiabilities")),
                "interest_cover": _ratio(op, abs(val(d, "InterestExpense") or 0) or None),
            },
        }
        periods.append(entry)
    periods.reverse()  # newest first
    latest_end = date.fromisoformat(periods[0]["period_end"])
    stale_after = 200 if period == "quarterly" else 520
    freshness = None
    if (today_ist() - latest_end).days > stale_after:
        freshness = (
            f"The newest {period} period in this source ends {latest_end.isoformat()}; newer results have "
            "probably been published. Check get_announcements with keyword='results'."
        )
    return {
        "symbol": sym,
        "period": period,
        "periods": periods,
        "freshness_warning": freshness,
        "units": "₹ crore, except EPS (₹) and percentages",
        "notes": (
            "Figures are as reported (consolidated where available). Indian companies publish full balance "
            "sheets and cash flows half-yearly, so quarterly rows often lack them. ROE uses year-end equity."
        ),
        "source": "Yahoo Finance (from company filings)",
    }


def _growth(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    if old < 0:  # growth off a loss is not meaningful as a percentage
        return None
    return round(100.0 * (new / old - 1.0), 2)


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round(a / b, 2)


# --- Shareholding --------------------------------------------------------------------------------


async def get_shareholding(svc: Services, symbol: str, quarters: int = 8) -> dict[str, Any]:
    sym = normalize_equity_symbol(symbol)
    rows = await svc.nse.shareholding(sym)
    if not rows:
        raise await not_found_with_suggestions(svc, sym)
    latest: dict[date, dict] = {}
    for r in rows:
        d = parse_date(r.get("date"))
        if not d:
            continue
        # Revised filings repeat a quarter; keep the most recently broadcast one.
        b = parse_date((r.get("broadcastDate") or "")[:11]) or d
        if d not in latest or b >= latest[d]["_b"]:
            latest[d] = {**r, "_b": b}
    ordered = sorted(latest.items(), reverse=True)
    history = [
        {
            "quarter_end": d.isoformat(),
            "promoter_pct": rnd(r.get("pr_and_prgrp")),
            "public_pct": rnd(r.get("public_val")),
            "employee_trusts_pct": rnd(r.get("employeeTrusts")) or None,
            "filed": iso(r["_b"]),
        }
        for d, r in ordered[:quarters]
    ]
    out: dict[str, Any] = {"symbol": sym, "name": rows[0].get("name"), "quarters": history}
    if len(history) >= 2 and history[0]["promoter_pct"] is not None:
        latest_p = history[0]["promoter_pct"]
        out["promoter_change_pts"] = {
            "qoq": _diff(latest_p, history[1]["promoter_pct"]),
            "yoy": _diff(latest_p, history[4]["promoter_pct"]) if len(history) > 4 else None,
        }
    out["notes"] = "NSE's summary splits holdings into promoter group and public only."
    out["source"] = "NSE shareholding pattern filings"
    return out


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 2)


# --- Corporate actions ---------------------------------------------------------------------------

_ACTION_TYPES = [
    ("dividend", re.compile(r"dividend", re.I)),
    ("bonus", re.compile(r"bonus", re.I)),
    ("split", re.compile(r"split|sub-?division", re.I)),
    ("rights", re.compile(r"rights", re.I)),
    ("buyback", re.compile(r"buy\s*-?back", re.I)),
    ("demerger", re.compile(r"demerger|scheme of arrangement", re.I)),
    ("meeting", re.compile(r"general meeting|\bagm\b|\begm\b", re.I)),
    ("interest_payment", re.compile(r"interest payment|redemption", re.I)),
]
_AMOUNT = re.compile(r"(?:rs|re|inr|₹)\.?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/-)?\s*(?:per\s+share|ps)?", re.I)


def classify_action(subject: str) -> tuple[str, float | None]:
    kind = next((name for name, rx in _ACTION_TYPES if rx.search(subject or "")), "other")
    amount = None
    if kind == "dividend":
        amounts = [float(m.group(1)) for m in _AMOUNT.finditer(subject)]
        amount = sum(amounts) if amounts else None
    return kind, amount


async def get_corporate_actions(svc: Services, symbol: str, limit: int = 15) -> dict[str, Any]:
    sym = normalize_equity_symbol(symbol)
    rows = await svc.nse.corporate_actions(sym)
    if not rows:
        try:
            await svc.nse.meta(sym)
        except NotFoundError:
            raise await not_found_with_suggestions(svc, sym) from None
        return {
            "symbol": sym,
            "actions": [],
            "notes": "NSE lists no corporate actions for this symbol.",
            "source": "NSE",
        }
    today = today_ist()
    actions = []
    for r in rows:
        subject = (r.get("subject") or "").strip()
        kind, amount = classify_action(subject)
        ex = parse_date(r.get("exDate"))
        actions.append(
            {
                "ex_date": iso(ex),
                "record_date": iso(parse_date(r.get("recDate"))),
                "type": kind,
                "detail": subject,
                "dividend_per_share": amount,
                "upcoming": bool(ex and ex >= today) or None,
            }
        )
    actions.sort(key=lambda a: a["ex_date"] or "", reverse=True)
    year_ago = (today - timedelta(days=365)).isoformat()
    ttm = [
        a["dividend_per_share"]
        for a in actions
        if a["type"] == "dividend"
        and a["dividend_per_share"]
        and (a["ex_date"] or "") > year_ago
        and (a["ex_date"] or "") <= today.isoformat()
    ]
    out: dict[str, Any] = {"symbol": sym, "name": rows[0].get("comp"), "actions": actions[:limit]}
    if ttm:
        out["dividends_last_12m_per_share"] = round(sum(ttm), 2)
        try:
            quote = await get_quote(svc, sym)
            if quote.get("price"):
                out["trailing_dividend_yield_pct"] = round(100.0 * sum(ttm) / quote["price"], 2)
                out["price_used"] = quote["price"]
        except MarketDataError:
            pass
    out["source"] = "NSE"
    return out


# --- Announcements -------------------------------------------------------------------------------


async def get_announcements(
    svc: Services, symbol: str, days: int = 30, limit: int = 20, keyword: str | None = None
) -> dict[str, Any]:
    if not 1 <= days <= 365:
        raise InvalidInputError("days must be between 1 and 365.")
    sym = normalize_equity_symbol(symbol)
    today = today_ist()
    start = today - timedelta(days=days)
    rows = await svc.nse.announcements(sym, start.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y"))
    items = []
    kw = (keyword or "").strip().lower()
    for r in rows:
        category = (r.get("desc") or "").strip()
        text = " ".join((r.get("attchmntText") or "").split())
        if kw and kw not in category.lower() and kw not in text.lower():
            continue
        items.append(
            {
                "time": r.get("an_dt"),
                "category": category,
                "summary": text[:400] + ("…" if len(text) > 400 else ""),
                "document": r.get("attchmntFile") or None,
            }
        )
    if not items and not rows:
        try:
            await svc.nse.meta(sym)
        except NotFoundError:
            raise await not_found_with_suggestions(svc, sym) from None
    return {
        "symbol": sym,
        "from": start.isoformat(),
        "to": today.isoformat(),
        "count": len(items),
        "announcements": items[:limit],
        "note": f"Showing {limit} of {len(items)}; narrow with `keyword` or `days`." if len(items) > limit else None,
        "source": "NSE corporate filings",
    }
