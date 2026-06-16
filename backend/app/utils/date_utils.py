from datetime import date, datetime
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo


def get_week_of_year(target_date: date) -> Tuple[int, int]:
    """
    获取指定日期所在的年份和周数
    
    Args:
        target_date: 目标日期
        
    Returns:
        Tuple[int, int]: (周数, 年份)
    """
    # 使用ISO周数计算（周一为每周第一天）
    year, week, _ = target_date.isocalendar()
    return week, year


def get_week_range_from_date(target_date: date) -> Tuple[date, date]:
    """
    根据指定日期获取该周的开始和结束日期（周一到周日）
    
    Args:
        target_date: 目标日期
        
    Returns:
        Tuple[date, date]: (周开始日期, 周结束日期)
    """
    # 计算到本周一的天数
    days_since_monday = target_date.weekday()
    monday = target_date - datetime.timedelta(days=days_since_monday)
    sunday = monday + datetime.timedelta(days=6)
    
    return monday, sunday

    
def convert_utc_to_local_timezone(utc_datetime: Any) -> str:
    """
    将UTC时间转换为本地时区(Asia/Shanghai)的字符串格式
    
    Args:
        utc_datetime: UTC时间，可能是datetime对象或字符串
        
    Returns:
        转换后的本地时间字符串，如果转换失败则返回"--"
    """
    if not utc_datetime:
        return "--"
    
    try:
        # 如果是字符串，先转换为datetime对象
        if isinstance(utc_datetime, str):
            # 尝试解析ISO格式的字符串
            if 'T' in utc_datetime:
                dt = datetime.fromisoformat(utc_datetime.replace('Z', '+00:00'))
            else:
                # 尝试其他常见格式
                dt = datetime.fromisoformat(utc_datetime)
        elif isinstance(utc_datetime, datetime):
            dt = utc_datetime
        else:
            return "--"
        
        # 如果datetime对象没有时区信息，假设它是UTC时间
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        
        # 转换为Asia/Shanghai时区
        local_dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        
        # 返回格式化的时间字符串
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        
    except Exception as e:
        return "--"


def convert_beijing_date_to_utc_range(beijing_date_str: str, is_start: bool = True) -> Optional[datetime]:
    """
    将北京时间的日期字符串转换为UTC时间
    
    Args:
        beijing_date_str: 北京时间的日期字符串，格式为 "YYYY-MM-DD"
        is_start: True表示开始时间（00:00:00），False表示结束时间（23:59:59）
        
    Returns:
        UTC时间对象，如果解析失败则返回None
    """
    try:
        # 解析北京时间的日期
        beijing_date = datetime.strptime(beijing_date_str, "%Y-%m-%d").date()
        
        # 根据is_start参数选择时间
        if is_start:
            # 开始时间：00:00:00
            beijing_datetime = datetime.combine(beijing_date, datetime.min.time())
        else:
            # 结束时间：23:59:59
            beijing_datetime = datetime.combine(beijing_date, datetime.max.time().replace(microsecond=0))
        
        # 转换为UTC时间
        beijing_tz = ZoneInfo("Asia/Shanghai")
        utc_tz = ZoneInfo("UTC")
        beijing_datetime = beijing_datetime.replace(tzinfo=beijing_tz)
        utc_datetime = beijing_datetime.astimezone(utc_tz)
        
        return utc_datetime
    except ValueError:
        return None


_BEIJING_TZ = ZoneInfo("Asia/Shanghai")

def beijing_today_date():
    """获取北京时间的“今天”（date）。"""
    return datetime.now(_BEIJING_TZ).date()


def beijing_now() -> datetime:
    """获取当前北京时间（aware datetime）。"""
    return datetime.now(_BEIJING_TZ)


def parse_beijing_time_hhmm(value: str) -> Optional[tuple[int, int]]:
    """解析北京时间 HH:MM，无效或空字符串返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError:
        return None
    return parsed.hour, parsed.minute


def is_before_daily_cutoff(
    *,
    now: Optional[datetime] = None,
    daily_cutoff_time: Optional[str] = None,
) -> bool:
    """
    当前北京时间是否早于每日修改截止时间（不含截止时刻本身）。

    daily_cutoff_time 为空表示不限制。
    """
    if daily_cutoff_time is None:
        from app.core.config import settings

        daily_cutoff_time = settings.CRM_VISIT_RECORD_REVISE_DAILY_CUTOFF_TIME
    parsed = parse_beijing_time_hhmm(daily_cutoff_time or "")
    if parsed is None:
        return True
    hour, minute = parsed
    ref = now or beijing_now()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_BEIJING_TZ)
    else:
        ref = ref.astimezone(_BEIJING_TZ)
    cutoff = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return ref < cutoff


def utc_datetime_to_beijing_date(utc_datetime: Any) -> Optional[date]:
    """将库中 UTC（naive 或 aware）时间折算为北京时间日期。"""
    if not utc_datetime:
        return None
    local_str = convert_utc_to_local_timezone(utc_datetime)
    if not local_str or local_str == "--":
        return None
    try:
        return datetime.strptime(local_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _visit_record_revise_entry_days_denial_reason(
    created_beijing_date: date,
    *,
    today: date,
    entry_window_days: int,
) -> Optional[str]:
    age_days = (today - created_beijing_date).days
    if age_days < 0:
        return "录入时间不在允许修改的范围内"
    if entry_window_days < 0:
        return None
    if entry_window_days == 0 and age_days != 0:
        return "仅支持修改当日录入的拜访记录"
    if entry_window_days > 0 and age_days >= entry_window_days:
        if entry_window_days == 1:
            return "仅支持修改当日录入的拜访记录"
        return f"仅支持修改最近 {entry_window_days} 个自然日内录入的拜访记录"
    return None


def get_visit_record_revise_entry_denial_reason(
    created_beijing_date: Optional[date],
    *,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    entry_window_days: Optional[int] = None,
    daily_cutoff_time: Optional[str] = None,
) -> Optional[str]:
    """返回不允许修改时的用户提示；允许修改则返回 None。"""
    from app.core.config import settings

    if entry_window_days is None:
        entry_window_days = settings.CRM_VISIT_RECORD_REVISE_ENTRY_WINDOW_DAYS
    if daily_cutoff_time is None:
        daily_cutoff_time = settings.CRM_VISIT_RECORD_REVISE_DAILY_CUTOFF_TIME
    if created_beijing_date is None:
        return "录入时间不在允许修改的范围内"

    ref_today = today or beijing_today_date()
    days_denial = _visit_record_revise_entry_days_denial_reason(
        created_beijing_date,
        today=ref_today,
        entry_window_days=entry_window_days,
    )
    if days_denial:
        return days_denial
    if not is_before_daily_cutoff(now=now, daily_cutoff_time=daily_cutoff_time):
        cutoff_label = (daily_cutoff_time or "").strip() or "截止时间"
        return f"每日 {cutoff_label} 之后不可修改拜访记录"
    return None


def is_visit_record_revise_entry_allowed(
    created_beijing_date: Optional[date],
    *,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    entry_window_days: Optional[int] = None,
    daily_cutoff_time: Optional[str] = None,
) -> bool:
    """
    判断拜访记录是否落在允许修改的时间窗口内。

    entry_window_days（默认读 settings.CRM_VISIT_RECORD_REVISE_ENTRY_WINDOW_DAYS）：
    - <0：不限制自然日
    - 0：仅当日录入
    - >0：录入日起连续 N 个自然日内（含录入当天）

    daily_cutoff_time（默认读 settings.CRM_VISIT_RECORD_REVISE_DAILY_CUTOFF_TIME）：
    北京时间 HH:MM，到达该时刻起当日不可再修改；空表示不限制。
    """
    return (
        get_visit_record_revise_entry_denial_reason(
            created_beijing_date,
            today=today,
            now=now,
            entry_window_days=entry_window_days,
            daily_cutoff_time=daily_cutoff_time,
        )
        is None
    )


def visit_record_revise_entry_window_hint(
    created_beijing_date: Optional[date] = None,
    *,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    entry_window_days: Optional[int] = None,
    daily_cutoff_time: Optional[str] = None,
) -> str:
    """返回录入时间窗口相关的用户提示文案。"""
    denial = get_visit_record_revise_entry_denial_reason(
        created_beijing_date,
        today=today,
        now=now,
        entry_window_days=entry_window_days,
        daily_cutoff_time=daily_cutoff_time,
    )
    if denial:
        return denial
    return "录入时间不在允许修改的范围内"