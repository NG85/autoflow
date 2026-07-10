from typing import Dict, Optional, Any
from sqlmodel import Session, select
from app.models.crm_system_configurations import CRMSystemConfiguration
import logging

logger = logging.getLogger(__name__)


class CRMConfigService:
    """CRM配置服务类"""
    
    def __init__(self, db_session: Session):
        self.db_session = db_session
    
    def get_config_value(self, config_type: str, config_key: str, default_value: Optional[str] = None) -> Optional[str]:
        """
        获取配置值
        
        Args:
            config_type: 配置类型
            config_key: 配置键
            default_value: 默认值
            
        Returns:
            配置值或默认值
        """
        try:
            stmt = select(CRMSystemConfiguration).where(
                CRMSystemConfiguration.config_type == config_type,
                CRMSystemConfiguration.config_key == config_key,
                CRMSystemConfiguration.is_active == True
            )
            config = self.db_session.exec(stmt).first()
            
            if config:
                return config.config_value
            else:
                logger.debug(f"配置未找到: {config_type}.{config_key}, 使用默认值: {default_value}")
                return default_value
                
        except Exception as e:
            logger.error(f"获取配置失败: {config_type}.{config_key}, 错误: {e}")
            return default_value
    
    def get_config_dict(self, config_type: str) -> Dict[str, str]:
        """
        获取指定类型的所有配置，返回字典格式
        
        Args:
            config_type: 配置类型
            
        Returns:
            配置字典 {config_key: config_value}
        """
        try:
            stmt = select(CRMSystemConfiguration).where(
                CRMSystemConfiguration.config_type == config_type,
                CRMSystemConfiguration.is_active == True
            )
            configs = self.db_session.exec(stmt).all()
            
            return {config.config_key: config.config_value for config in configs}
            
        except Exception as e:
            logger.error(f"获取配置字典失败: {config_type}, 错误: {e}")
            return {}
    
    def get_field_mapping_config(self) -> Dict[str, str]:
        """
        获取字段名映射配置
        
        Returns:
            字段名映射字典
        """
        return self.get_config_dict("VisitRecordFieldMapping")


def get_crm_config_service(db_session: Session) -> CRMConfigService:
    """获取CRM配置服务实例"""
    return CRMConfigService(db_session)


DEFAULT_VISIT_RECORD_FIELD_MAPPING: Dict[str, str] = {
    "partner_title": "合作伙伴",
    "opportunity_title": "商机名称",
    "account_title": "最终客户",
    "followup_title": "跟进记录",
    "next_steps_title": "下一步计划",
    "partner_title_en": "Partner",
    "opportunity_title_en": "Opportunity",
    "account_title_en": "End Customer",
    "followup_title_en": "Follow-up Record",
    "next_steps_title_en": "Next Steps",
    # 兼容旧 key（与 *_title 默认同值；解析后仍会从 *_title 同步）
    "partner": "合作伙伴",
    "end_customer": "最终客户",
}

# 线索类字段（lead_title / lead_title_en）：无内置默认值，仅以 DB VisitRecordFieldMapping 为准；
# 未配置时不会出现在生效映射中，前端/筛选也不展示线索选项。

# alias_key -> source_key：DB 只改 *_title 时，别名跟随生效
_FIELD_MAPPING_ALIASES: tuple[tuple[str, str], ...] = (
    ("partner", "partner_title"),
    ("end_customer", "account_title"),
    ("lead", "lead_title"),
)

# 跟进对象类型 filter-options / customer_attribute 可选键（lead 无默认值，未配置时不出现）
FOLLOWUP_OBJECT_ATTRIBUTE_KEYS: tuple[str, ...] = ("end_customer", "partner", "lead")


def get_resolved_field_mapping(db_session: Session, report_type: str = "报告") -> Dict[str, str]:
    """
    获取生效的字段标题映射：默认值 + crm_system_configurations.VisitRecordFieldMapping 覆盖。
    """
    field_title_mapping = DEFAULT_VISIT_RECORD_FIELD_MAPPING.copy()
    try:
        config_service = get_crm_config_service(db_session)
        db_field_mapping = config_service.get_field_mapping_config()
        if db_field_mapping:
            field_title_mapping.update(db_field_mapping)
            logger.info(f"{report_type}使用数据库字段映射配置: {db_field_mapping}")
        else:
            logger.info(f"{report_type}未找到数据库字段映射配置，使用默认配置")
    except Exception as e:
        logger.warning(f"{report_type}获取数据库字段映射配置失败，使用默认配置: {e}")

    for alias_key, source_key in _FIELD_MAPPING_ALIASES:
        if source_key in field_title_mapping:
            field_title_mapping[alias_key] = field_title_mapping[source_key]

    return field_title_mapping


def build_customer_attribute_options(field_mapping: Dict[str, str]) -> Dict[str, str]:
    """构建跟进对象类型筛选项：仅包含字段映射中已配置展示名的类型。"""
    return {
        key: label
        for key in FOLLOWUP_OBJECT_ATTRIBUTE_KEYS
        if (label := (field_mapping.get(key) or "").strip())
    }


def add_field_mapping_to_data(data: Dict[str, Any], db_session: Session, report_type: str = "报告") -> Dict[str, Any]:
    """
    为数据添加字段名映射，用于卡片展示
    
    Args:
        data: 要添加字段映射的数据
        db_session: 数据库会话
        report_type: 报告类型，用于日志记录
        
    Returns:
        添加了字段映射的数据
    """
    field_title_mapping = get_resolved_field_mapping(db_session, report_type)

    # 将字段名映射添加到数据中
    for field_key, field_label in field_title_mapping.items():
        data[field_key] = field_label
    
    return data
