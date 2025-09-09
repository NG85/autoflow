import httpx
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.core.config import settings
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.utils.date_utils import convert_utc_to_local_timezone

logger = logging.getLogger(__name__)


class CrmWritebackClient:
    """CRM数据回写客户端"""
    
    def __init__(self, base_url: str = "http://auth:8018"):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json"
        }
    
    def single_writeback(self, writeback_type: str, unique_id: str, content: str, 
                        write_mode: str = "APPEND", operator: str = None) -> Dict[str, Any]:
        """
        单个对象回写
        
        Args:
            writeback_type: 回写类型 ("ACCOUNT" 或 "OPPORTUNITY")
            unique_id: 对象唯一ID
            content: 回写内容
            write_mode: 回写模式 ("APPEND", "REPLACE", "PREPEND")
            operator: 操作人
        
        Returns:
            回写结果
        """
        url = f"{self.base_url}/crm/writeback/single"
        
        payload = {
            "writebackType": writeback_type,
            "uniqueId": unique_id,
            "content": content,
            "writeMode": write_mode,
            "operator": operator
        }
        
        try:
            with httpx.Client() as client:
                response = client.post(url, headers=self.headers, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            logger.error(f"请求失败: {e}")
            return {"success": False, "message": f"请求失败: {e}"}
    
    def batch_writeback(self, requests_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量对象回写
        
        Args:
            requests_list: 回写请求列表
        
        Returns:
            回写结果列表
        """
        url = f"{self.base_url}/crm/writeback/batch"
        
        try:
            with httpx.Client() as client:
                response = client.post(url, headers=self.headers, json=requests_list)
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            logger.error(f"批量请求失败: {e}")
            return [{"success": False, "message": f"批量请求失败: {e}"}]
    
    def get_writeback_log(self, log_id: int) -> Dict[str, Any]:
        """
        查询回写日志
        
        Args:
            log_id: 日志ID
        
        Returns:
            日志详情
        """
        url = f"{self.base_url}/crm/writeback/log/{log_id}"
        
        try:
            with httpx.Client() as client:
                response = client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.RequestError as e:
            logger.error(f"查询日志失败: {e}")
            return {"success": False, "message": f"查询日志失败: {e}"}


class CrmWritebackService:
    """CRM数据回写服务"""
    
    def __init__(self):
        self.client = CrmWritebackClient(settings.CRM_WRITEBACK_API_URL)
    
    def generate_visit_summary_content(self, visit_records: List[CRMSalesVisitRecord]) -> str:
        """
        根据拜访记录生成回写内容
        
        Args:
            visit_records: 拜访记录列表
        
        Returns:
            格式化的回写内容
        """
        if not visit_records:
            return ""
        
        content_parts = []
        
        # 按拜访日期降序排序（最新的在前面）
        sorted_records = sorted(visit_records, key=lambda x: x.visit_communication_date, reverse=True)
        
        for record in sorted_records:
            # 拜访基本信息
            content_parts.append(f"拜访日期: {record.visit_communication_date}")
            
            if record.last_modified_time:
                formatted_time = convert_utc_to_local_timezone(record.last_modified_time)
                if formatted_time != "--":
                    content_parts.append(f"创建时间: {formatted_time}")
            
            if record.subject:
                content_parts.append(f"拜访主题: {record.subject}")
            
            if record.contact_name:
                content_parts.append(f"客户联系人: {record.contact_name}")
                if record.contact_position:
                    content_parts.append(f"客户职位: {record.contact_position}")
            
            if record.collaborative_participants:
                # 处理协同参与人数据，支持多种格式
                participant_names = []
                
                if isinstance(record.collaborative_participants, str):
                    # 如果是字符串，尝试解析为JSON
                    try:
                        import json
                        parsed = json.loads(record.collaborative_participants)
                        if isinstance(parsed, list):
                            # JSON数组格式
                            for participant in parsed:
                                if isinstance(participant, dict) and participant.get("name"):
                                    participant_names.append(participant["name"])
                                else:
                                    participant_names.append(str(participant))
                        else:
                            # 非数组格式，按原字符串处理
                            participant_names.append(record.collaborative_participants)
                    except (json.JSONDecodeError, TypeError):
                        # 解析失败，按原字符串处理
                        participant_names.append(record.collaborative_participants)
                elif isinstance(record.collaborative_participants, list):
                    # 已经是列表格式
                    for participant in record.collaborative_participants:
                        if isinstance(participant, dict) and participant.get("name"):
                            participant_names.append(participant["name"])
                        else:
                            participant_names.append(str(participant))
                else:
                    # 其他格式，转换为字符串
                    participant_names.append(str(record.collaborative_participants))
                
                if participant_names:
                    content_parts.append(f"协同参与人: {', '.join(participant_names)}")
            
            # 跟进记录
            if record.followup_record:
                content_parts.append("跟进记录:")
                content_parts.append(record.followup_record)
            
            # 下一步计划
            if record.next_steps:
                content_parts.append("下一步计划:")
                content_parts.append(record.next_steps)
            
            # 备注
            if record.remarks:
                content_parts.append("备注:")
                content_parts.append(record.remarks)
            
            content_parts.append("---")
        
        return "\n".join(content_parts)
    
    def writeback_visit_records(self, session: Session, start_date: datetime.date, 
                               end_date: datetime.date) -> Dict[str, Any]:
        """
        回写指定时间范围内的拜访记录
        
        Args:
            session: 数据库会话
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            回写结果
        """
        try:
            # 查询指定时间范围内的拜访记录
            stmt = select(CRMSalesVisitRecord).where(
                CRMSalesVisitRecord.visit_communication_date >= start_date,
                CRMSalesVisitRecord.visit_communication_date <= end_date
            ).order_by(CRMSalesVisitRecord.visit_communication_date)
            
            visit_records = session.exec(stmt).all()
            
            if not visit_records:
                logger.info(f"在 {start_date} 到 {end_date} 时间范围内没有找到拜访记录")
                return {
                    "success": True,
                    "message": f"在 {start_date} 到 {end_date} 时间范围内没有找到拜访记录",
                    "processed_count": 0,
                    "writeback_count": 0
                }
            
            logger.info(f"找到 {len(visit_records)} 条拜访记录，开始进行回写")
            logger.info("回写优先级：商机 > 客户 > 合作伙伴")
            
            # 按优先级分组：商机 > 客户 > 合作伙伴
            opportunity_records = {}
            account_records = {}
            partner_records = {}
            
            for record in visit_records:
                # 优先级1：如果有商机ID，按商机分组
                if record.opportunity_id:
                    if record.opportunity_id not in opportunity_records:
                        opportunity_records[record.opportunity_id] = []
                    opportunity_records[record.opportunity_id].append(record)
                # 优先级2：如果没有商机但有客户ID，按客户分组
                elif record.account_id:
                    if record.account_id not in account_records:
                        account_records[record.account_id] = []
                    account_records[record.account_id].append(record)
                # 优先级3：如果既没有商机也没有客户，但有合作伙伴ID，按合作伙伴分组
                elif record.partner_id:
                    if record.partner_id not in partner_records:
                        partner_records[record.partner_id] = []
                    partner_records[record.partner_id].append(record)
            
            # 记录分组统计信息
            logger.info(f"分组结果：商机 {len(opportunity_records)} 个，客户 {len(account_records)} 个，合作伙伴 {len(partner_records)} 个")
            
            # 准备批量回写请求
            writeback_requests = []
            processed_count = 0
            
            # 处理商机回写
            for opportunity_id, records in opportunity_records.items():
                content = self.generate_visit_summary_content(records)
                writeback_requests.append({
                    "writebackType": "OPPORTUNITY",
                    "uniqueId": opportunity_id,
                    "content": content,
                    "writeMode": "PREPEND",
                    "operator": "system"
                })
                processed_count += len(records)
                logger.info(f"准备回写商机 {opportunity_id}，包含 {len(records)} 条拜访记录")
            
            # 处理客户回写
            for account_id, records in account_records.items():
                content = self.generate_visit_summary_content(records)
                writeback_requests.append({
                    "writebackType": "ACCOUNT",
                    "uniqueId": account_id,
                    "content": content,
                    "writeMode": "PREPEND",
                    "operator": "system"
                })
                processed_count += len(records)
                logger.info(f"准备回写客户 {account_id}，包含 {len(records)} 条拜访记录")
            
            # 处理合作伙伴回写（合作伙伴和客户使用同一个API）
            for partner_id, records in partner_records.items():
                content = self.generate_visit_summary_content(records)
                writeback_requests.append({
                    "writebackType": "ACCOUNT",
                    "uniqueId": partner_id,
                    "content": content,
                    "writeMode": "PREPEND",
                    "operator": "system"
                })
                processed_count += len(records)
                logger.info(f"准备回写合作伙伴 {partner_id}，包含 {len(records)} 条拜访记录")
            
            # 执行批量回写
            if writeback_requests:
                results = self.client.batch_writeback(writeback_requests)
                
                # 统计成功和失败的数量
                success_count = sum(1 for result in results if result.get("success", False))
                failed_count = len(results) - success_count
                
                logger.info(f"批量回写完成: 成功 {success_count} 个，失败 {failed_count} 个")
                
                return {
                    "success": True,
                    "message": f"成功处理 {processed_count} 条拜访记录，回写 {len(writeback_requests)} 个对象",
                    "processed_count": processed_count,
                    "writeback_count": len(writeback_requests),
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "results": results
                }
            else:
                logger.warning("没有需要回写的对象")
                return {
                    "success": True,
                    "message": "没有需要回写的对象",
                    "processed_count": processed_count,
                    "writeback_count": 0
                }
                
        except Exception as e:
            logger.exception(f"回写拜访记录失败: {e}")
            return {
                "success": False,
                "message": f"回写失败: {str(e)}",
                "processed_count": 0,
                "writeback_count": 0
            }


# 创建全局服务实例
crm_writeback_service = CrmWritebackService()
