"""
CRM动态字段定义
"""

from typing import List, Dict, Any
from datetime import datetime
from app.models.crm_dynamic_fields import CRMDynamicFieldsMixin


# 从混入类中获取动态字段列表
def get_dynamic_fields_list() -> List[str]:
    """
    获取动态字段列表
    
    Returns:
        动态字段名称列表
    """
    # 从混入类的字段中提取动态字段
    dynamic_fields = []
    for field_name, field_info in CRMDynamicFieldsMixin.__annotations__.items():
        if not field_name.startswith('_'):  # 排除私有字段
            dynamic_fields.append(field_name)
    return dynamic_fields


# 动态字段列表
DYNAMIC_FIELDS = get_dynamic_fields_list()


def generate_dynamic_fields_array(visit_record: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    根据拜访记录数据生成dynamic_fields数组
    
    Args:
        visit_record: 拜访记录数据
        
    Returns:
        dynamic_fields数组，格式如：
        [
            {
                "key": "起止时间",
                "value": "10:00:00 - 11:30:00"
            }
        ]
    """
    dynamic_fields_array = []
    
    # 特殊处理：时间范围组合字段
    visit_start_time = visit_record.get('visit_start_time')
    visit_end_time = visit_record.get('visit_end_time')
    
    # 只有当开始时间和结束时间都有值时才生成时间范围字段
    if visit_start_time and visit_end_time:
        # 格式化开始时间 - 从字符串中提取时间部分
        if isinstance(visit_start_time, str):
            # 从 "2025-09-18 05:00:00" 中提取 "05:00:00"
            try:
                start_time_str = visit_start_time.split(' ')[1] if ' ' in visit_start_time else visit_start_time
            except:
                start_time_str = str(visit_start_time).strip()
        else:
            start_time_str = str(visit_start_time).strip()
        
        # 格式化结束时间 - 从字符串中提取时间部分
        if isinstance(visit_end_time, str):
            # 从 "2025-09-18 08:00:00" 中提取 "08:00:00"
            try:
                end_time_str = visit_end_time.split(' ')[1] if ' ' in visit_end_time else visit_end_time
            except:
                end_time_str = str(visit_end_time).strip()
        else:
            end_time_str = str(visit_end_time).strip()
        
        # 组合时间范围
        time_range_value = f"{start_time_str} ~ {end_time_str}"
        
        # 使用固定的标签
        dynamic_fields_array.append({
            "key": "起止时间",
            "key_en": "Time Range",
            "value": time_range_value,
            "value_en": time_range_value
        })
    
    # 字段名到中文标签的映射
    field_label_map = {
        "visit_purpose": "拜访目的",
        "record_type": "记录类型",
    }
    
    # 字段名到英文标签的映射
    field_label_en_map = {
        "visit_purpose": "Visit Purpose",
        "record_type": "Record Type",
    }
    
    # 处理其他动态字段（排除时间相关字段，因为已经组合处理了）
    for field_key in DYNAMIC_FIELDS:
        # 跳过时间字段，因为已经在上面的特殊处理中处理了
        if field_key in ['visit_start_time', 'visit_end_time']:
            continue
            
        field_value = visit_record.get(field_key)
        
        # 只有当字段值不为空且不为'--'时才添加到数组中
        if field_value and str(field_value).strip() not in ('', '--', 'None'):
            # 特殊处理：record_type 字段需要转换为中英文显示
            if field_key == 'record_type':
                from app.api.routes.crm.models import RecordType
                # 处理枚举类型：如果是枚举对象，先获取其值
                if hasattr(field_value, 'value'):
                    # 如果是枚举对象，获取其值
                    field_value_str = field_value.value
                else:
                    field_value_str = str(field_value)
                
                record_type_enum = RecordType.from_english(field_value_str)
                if record_type_enum:
                    formatted_value = record_type_enum.chinese
                    formatted_value_en = record_type_enum.english
                else:
                    # 如果不是枚举值，尝试中文转换
                    record_type_enum = RecordType.from_chinese(field_value_str)
                    if record_type_enum:
                        formatted_value = record_type_enum.chinese
                        formatted_value_en = record_type_enum.english
                    else:
                        # 都不匹配，使用原值
                        formatted_value = field_value_str.strip()
                        formatted_value_en = formatted_value
            else:
                # 处理datetime类型，格式化为字符串
                if hasattr(field_value, 'strftime'):
                    # 如果是datetime对象，格式化为时间字符串
                    formatted_value = field_value.strftime("%H:%M:%S")
                    formatted_value_en = formatted_value
                else:
                    # 如果是字符串，直接使用
                    formatted_value = str(field_value).strip()
                    formatted_value_en = formatted_value
            
            # 确保格式化后的值不为空且不为'--'
            if formatted_value and str(formatted_value).strip() not in ('', '--', 'None'):
                # 获取字段的中文和英文标签
                field_label = field_label_map.get(field_key, field_key)
                field_label_en = field_label_en_map.get(field_key, field_key)
                
                # 添加到动态字段数组
                # 注意：record_type 有中英文值转换，其他字段的 value_en 和 value 相同（都是原值）
                dynamic_fields_array.append({
                    "key": field_label,
                    "key_en": field_label_en,
                    "value": formatted_value,
                    "value_en": formatted_value_en
                })
    
    return dynamic_fields_array
