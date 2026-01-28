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