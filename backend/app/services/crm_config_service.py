from typing import Dict, Optional
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
