from datetime import date, datetime
from typing import Any, Tuple
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
