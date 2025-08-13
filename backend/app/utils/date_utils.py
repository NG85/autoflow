from datetime import date, datetime
from typing import Tuple


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
