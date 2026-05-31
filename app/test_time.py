from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")


def tw_now() -> datetime:
    return datetime.now(TW_TZ)


def tw_today() -> date:
    return tw_now().date()


def days_from_today(days: int) -> date:
    return tw_today() + timedelta(days=days)


def cutoff_dt(pickup_date: date, *, minutes_after: int = 1) -> datetime:
    return datetime.combine(pickup_date - timedelta(days=1), time(17, minutes_after), tzinfo=TW_TZ)


def before_cutoff_dt(pickup_date: date) -> datetime:
    return datetime.combine(pickup_date - timedelta(days=1), time(16, 59), tzinfo=TW_TZ)


def after_cutoff_dt(pickup_date: date) -> datetime:
    return datetime.combine(pickup_date - timedelta(days=1), time(17, 1), tzinfo=TW_TZ)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
