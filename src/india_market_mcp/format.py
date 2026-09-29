"""Number, date and payload helpers shared by the sources and tools."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

# India has no daylight saving, so a fixed offset is exact (and avoids needing tzdata on Windows).
IST = timezone(timedelta(hours=5, minutes=30), "IST")

CRORE = 1e7


# Tests set this to pin "now" (expiry selection, date windows).
frozen_now: datetime | None = None


def now_ist() -> datetime:
    return frozen_now or datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def num(value: Any) -> float | None:
    """Parse NSE's mix of numbers, numeric strings, '-' and blanks."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) or math.isinf(value) else float(value)
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "NA", "N/A", "null", "None"}:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return None if math.isnan(out) or math.isinf(out) else out


def rnd(value: Any, digits: int = 2) -> float | None:
    v = num(value)
    return None if v is None else round(v, digits)


def crore(value: Any, digits: int = 2) -> float | None:
    """Rupees to ₹ crore."""
    v = num(value)
    return None if v is None else round(v / CRORE, digits)


def pct(part: float | None, whole: float | None, digits: int = 2) -> float | None:
    if part is None or whole in (None, 0):
        return None
    return round(100.0 * part / whole, digits)


def change_pct(new: float | None, old: float | None, digits: int = 2) -> float | None:
    if new is None or old in (None, 0):
        return None
    return round(100.0 * (new / old - 1.0), digits)


_DATE_FORMATS = (
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%B-%Y",
    "%d/%m/%Y",
)


def parse_date(text: Any) -> date | None:
    """Parse the date formats NSE, Yahoo and mfapi use ('29-Sep-2026', '29-SEP-2026 16:00:00', '2026-09-29'...)."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    if not t or t == "-":
        return None
    # NSE sometimes upper-cases the month ("16-JUL-2026").
    candidates = {t, t.title()}
    for cand in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def compact(value: Any) -> Any:
    """Drop None values and empty containers recursively so payloads stay small."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cv = compact(v)
            if cv is None or (isinstance(cv, (dict, list)) and not cv):
                continue
            out[k] = cv
        return out
    if isinstance(value, list):
        return [compact(v) for v in value if v is not None]
    return value
