import requests
import logging
from typing import Optional, Dict, Any
from app.core.config import settings

logger = logging.getLogger(__name__)


class SalesQuadrantsService:
    """销售四象限服务"""
    
    def __init__(self):
        self.base_url = settings.ALDEBARAN_BASE_URL
    
    def get_sales_quadrants(self, execution_id: str, tenant_id: str = "PINGCAP") -> Optional[Dict[str, Any]]:
        """
        获取销售四象限分布数据
        
        Args:
            execution_id: 报告执行ID
            tenant_id: 租户ID，默认为PINGCAP
            
        Returns:
            Dict: 销售四象限数据，如果获取失败返回None
        """
        try:
            url = f"{self.base_url}/api/v1/review/query"
            
            payload = {
                "tenant_id": tenant_id,
                "execution_id": execution_id,
                "return_fields": ["sales_quadrants"]
            }
            
            headers = {
                "Content-Type": "application/json"
            }
            
            logger.info(f"调用销售四象限接口: {url}, execution_id: {execution_id}")
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                # 解析嵌套的数据结构
                if data.get("status") == "success":
                    report_content = data.get("data", {}).get("report_content", {})
                    sales_quadrants = report_content.get("sales_quadrants")
                    
                    if sales_quadrants:
                        logger.info(f"成功获取销售四象限数据: {sales_quadrants}")
                        return sales_quadrants
                    else:
                        logger.warning(f"接口返回数据中没有sales_quadrants字段: {data}")
                        return None
                else:
                    logger.error(f"接口返回状态不是success: {data.get('message', '未知错误')}")
                    return None
            else:
                logger.error(f"调用销售四象限接口失败: status_code={response.status_code}, response={response.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"调用销售四象限接口超时: execution_id={execution_id}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"调用销售四象限接口异常: {e}")
            return None
        except Exception as e:
            logger.error(f"获取销售四象限数据失败: {e}")
            return None


# 创建服务实例
sales_quadrants_service = SalesQuadrantsService()
