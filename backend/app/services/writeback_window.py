"""拜访记录回写：数据窗口解析（与 Celery Beat cron 调度解耦）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
import pytz

from app.core.config import WritebackFrequency, settings


@dataclass(frozen=True)
class WritebackWindow:
    """统一回写时间窗口。

    ``start_utc`` / ``end_utc`` 为 naive UTC，与 ``last_modified_time`` 查询口径一致。
    ``mode``：
      - calendar：按日历天起止（本地 00:00 ~ 23:59:59.999999）
      - datetime：任意时刻窗口（含 interval 滚动）
    """

    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime
    mode: str  # "calendar" | "datetime"
    frequency: WritebackFrequency


def _ensure_aware(dt: datetime, tz) -> datetime:
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


def _to_naive_utc(aware_local: datetime) -> datetime:
    return aware_local.astimezone(pytz.UTC).replace(tzinfo=None)


def calendar_day_window(
    start_date: date,
    end_date: date,
    tz,
    *,
    frequency: WritebackFrequency = WritebackFrequency.DAILY,
) -> WritebackWindow:
    """日历天闭区间：本地 start 00:00:00 ~ end 23:59:59.999999。"""
    start_local = tz.localize(datetime.combine(start_date, datetime.min.time()))
    end_local = tz.localize(datetime.combine(end_date, datetime.max.time()))
    return WritebackWindow(
        start_local=start_local,
        end_local=end_local,
        start_utc=_to_naive_utc(start_local),
        end_utc=_to_naive_utc(end_local),
        mode="calendar",
        frequency=frequency,
    )


def datetime_window(
    start_local: datetime,
    end_local: datetime,
    tz,
    *,
    frequency: WritebackFrequency = WritebackFrequency.INTERVAL,
) -> WritebackWindow:
    """任意时刻窗口（闭区间，与现有 ``<= end`` 查询对齐）。"""
    start_aware = _ensure_aware(start_local, tz)
    end_aware = _ensure_aware(end_local, tz)
    if start_aware > end_aware:
        raise ValueError(f"无效时间窗口: start={start_aware} > end={end_aware}")
    return WritebackWindow(
        start_local=start_aware,
        end_local=end_aware,
        start_utc=_to_naive_utc(start_aware),
        end_utc=_to_naive_utc(end_aware),
        mode="datetime",
        frequency=frequency,
    )


def resolve_writeback_window(
    tz=None,
    frequency: Optional[WritebackFrequency] = None,
    *,
    lookback_minutes: Optional[int] = None,
    now: Optional[datetime] = None,
) -> WritebackWindow:
    """按 FREQUENCY 自动计算回写窗口。

    - weekly：上周日 ~ 本周六（相对 ``now`` 所在本地日）
    - daily：昨天
    - interval：``[now - lookback_minutes, now]`` 闭区间
    """
    tz = tz or pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
    frequency = frequency or settings.CRM_WRITEBACK_FREQUENCY
    lookback_minutes = (
        lookback_minutes
        if lookback_minutes is not None
        else settings.CRM_WRITEBACK_LOOKBACK_MINUTES
    )

    if now is None:
        now_local = datetime.now(tz)
    else:
        now_local = _ensure_aware(now, tz)

    if frequency == WritebackFrequency.INTERVAL:
        if lookback_minutes is None or lookback_minutes <= 0:
            raise ValueError(
                "interval 模式要求 CRM_WRITEBACK_LOOKBACK_MINUTES > 0"
            )
        end_local = now_local
        start_local = now_local - timedelta(minutes=lookback_minutes)
        return datetime_window(
            start_local, end_local, tz, frequency=WritebackFrequency.INTERVAL
        )

    today = now_local.date()
    if frequency == WritebackFrequency.DAILY:
        day = today - timedelta(days=1)
        return calendar_day_window(
            day, day, tz, frequency=WritebackFrequency.DAILY
        )

    # WEEKLY：上周日到本周六
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday + 7)
    this_saturday = last_sunday + timedelta(days=6)
    return calendar_day_window(
        last_sunday, this_saturday, tz, frequency=WritebackFrequency.WEEKLY
    )


def parse_datetime_in_writeback_tz(value: str, tz=None) -> datetime:
    """解析 datetime 字符串到回写时区（支持 ISO / 末尾 Z / 无偏移本地时间）。"""
    tz = tz or pytz.timezone(settings.CRM_WRITEBACK_TIMEZONE)
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # 兼容 "YYYY-MM-DD HH:MM:SS"
    if "T" not in s and " " in s and "+" not in s and s.count(":") >= 1:
        try:
            naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return tz.localize(naive)
        except ValueError:
            pass
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


def parse_crontab_fields(cron_expr: str) -> Optional[tuple[str, str, str, str, str]]:
    """解析 5 段 cron，非法返回 None。"""
    if not cron_expr or not str(cron_expr).strip():
        return None
    fields = str(cron_expr).strip().split()
    if len(fields) != 5:
        return None
    return tuple(fields)  # type: ignore[return-value]
