from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def resolve_report_date(value: str | None, timezone: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(ZoneInfo(timezone)).date() - timedelta(days=1)


def day_window(report_date: date, timezone: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    start = datetime.combine(report_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def in_day(dt: datetime | None, start: datetime, end: datetime) -> bool:
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=start.tzinfo)
    else:
        dt = dt.astimezone(start.tzinfo)
    return start <= dt < end

