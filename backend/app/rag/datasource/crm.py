from datetime import date, datetime
from typing import Dict, Generator, Optional
from sqlmodel import Session, select, func
from app.models import Document
from app.rag.datasource.base import BaseDataSource
from pydantic import BaseModel
import logging
from app.models.crm_accounts import CRMAccount
from app.models.crm_contacts import CRMContact
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_opportunity_updates import CRMOpportunityUpdate
from sqlalchemy import text
from app.core.db import get_db_session
from app.types import MimeTypes
from app.rag.datasource.crm_format import(
    format_account_info,
    format_contacts_info,
    format_opportunity_updates,
    get_column_comments_and_names
)
from app.models.document import DocumentCategory

logger = logging.getLogger(__name__)


class CRMDataSourceConfig(BaseModel):
    """Config for CRM data source"""
    include_accounts: bool = True
    include_contacts: bool = True
    include_updates: bool = True
    opportunity_filter: Optional[str] = None
    max_opportunities: Optional[int] = None
    batch_size: int = 100

class CRMDataSource(BaseDataSource):
    def validate_config(self):
        if isinstance(self.config, list):
            self.config_obj = CRMDataSourceConfig.model_validate(self.config[0])
        elif isinstance(self.config, dict):
            self.config_obj = CRMDataSourceConfig.model_validate(self.config)
        else:
            raise ValueError("config must be a list or dict")
    
    def load_documents(self, db_session: Optional[Session] = None) -> Generator[Document, None, None]:
        """Load crm documents from CRM database, each opportunity forms a Document."""
        # Use provided session or create a new session
        session_created = False
        if db_session is None:
            db_session = next(get_db_session())
            session_created = True
            
        try:
            # Get the total number of opportunities to batch process
            count_query = select(func.count(CRMOpportunity.id))
            if self.config_obj.opportunity_filter:
                count_query = count_query.where(text(self.config_obj.opportunity_filter))
            
            total_count = db_session.exec(count_query).one()
            logger.info(f"Found {total_count} opportunities matching filter")
            
            # Process opportunity number limit
            if self.config_obj.max_opportunities:
                total_count = min(total_count, self.config_obj.max_opportunities)
            
            # Batch process
            for offset in range(0, total_count, self.config_obj.batch_size):
                limit = min(self.config_obj.batch_size, total_count - offset)
                logger.info(f"Processing opportunities batch: offset={offset}, limit={limit}")
                
                # Get the opportunities of current batch
                query = select(CRMOpportunity)
                if self.config_obj.opportunity_filter:
                    query = query.where(text(self.config_obj.opportunity_filter))
                query = query.offset(offset).limit(limit)
                
                opportunities = db_session.exec(query).all()
                
                # Get all needed customer and contact data in advance
                customer_ids = []
                opportunity_ids = []
                
                for opp in opportunities:
                    if opp.customer_id:
                        customer_ids.append(opp.customer_id)
                    if opp.unique_id:
                        opportunity_ids.append(opp.unique_id)
                
                # Get all related customer and account data in advance
                accounts_map = {}
                if self.config_obj.include_accounts and customer_ids:
                    accounts = db_session.exec(
                        select(CRMAccount).where(CRMAccount.unique_id.in_(customer_ids))
                    ).all()
                    accounts_map = {acc.unique_id: acc for acc in accounts}
                
                # Get all related contact data in advance
                contacts_map = {}
                if self.config_obj.include_contacts and customer_ids:
                    contacts = db_session.exec(
                        select(CRMContact).where(CRMContact.customer_id.in_(customer_ids))
                    ).all()
                    # Group by customer ID
                    for contact in contacts:
                        if contact.customer_id not in contacts_map:
                            contacts_map[contact.customer_id] = []
                        contacts_map[contact.customer_id].append(contact)
                
                # Get all related opportunity update data in advance
                updates_map = {}
                if self.config_obj.include_updates and opportunity_ids:
                    updates = db_session.exec(
                        select(CRMOpportunityUpdate)
                        .where(CRMOpportunityUpdate.opportunity_id.in_(opportunity_ids))
                        .order_by(CRMOpportunityUpdate.record_date.desc())
                    ).all()
                    # Group by opportunity ID
                    for update in updates:
                        if update.opportunity_id not in updates_map:
                            updates_map[update.opportunity_id] = []
                        updates_map[update.opportunity_id].append(update)
                
                # Create a Document for each opportunity
                for opportunity in opportunities:
                    try:
                        # Get related data
                        account = accounts_map.get(opportunity.customer_id) if opportunity.customer_id else None
                        contacts = contacts_map.get(opportunity.customer_id, []) if opportunity.customer_id else []
                        updates = updates_map.get(opportunity.unique_id, []) if opportunity.unique_id else []
                        
                        # Create a Document
                        document = self._create_document(opportunity, account, contacts, updates)

                        # Return the created Document
                        yield document
                        
                    except Exception as e:
                        logger.error(f"Error processing opportunity {opportunity.unique_id}: {str(e)}")
                        continue
                
        finally:
            # Close the session we created ourselves
            if session_created and db_session:
                db_session.close()

    
    def _create_document(self, opportunity, account, contacts, updates) -> Document:
        """Create a Document object for a single opportunity."""
        
        # Create document content
        content = self._create_content(opportunity, account, contacts, updates)
        
        # Create metadata
        metadata = self._create_metadata(opportunity, account, contacts)
        
        # Create Document object
        return Document(
            name=f"{opportunity.customer_name or '未命名客户'} - {opportunity.opportunity_name or '未命名商机'}",
            hash=hash(content),
            content=content,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            source_uri="crm_database",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_modified_at=datetime.now(),
            meta=metadata,
        )
    
    def _create_content(self, opportunity, account, contacts, updates) -> str:
        """Create document content, including opportunity, customer and contact information."""
        content = []
        
        # Add opportunity basic information
        content.append(f"# 商机：{opportunity.opportunity_name}")
        # content.append(f"**商机ID**: {opportunity.unique_id}")
        content.append(f"**负责人**: {opportunity.owner}")
        content.append(f"**商机阶段**: {opportunity.opportunity_stage}")
        content.append(f"**阶段状态**: {opportunity.stage_status}")
        content.append(f"**预计成交日期**: {opportunity.expected_closing_date}")
        content.append(f"**预计成交季度**: {opportunity.expected_closing_quarter}")
        content.append(f"**预计成交月**: {opportunity.expected_closing_month}")
        content.append(f"**预测金额**: {opportunity.forecast_amount}")
        content.append(f"**业务类型**: {opportunity.business_type}")
        content.append(f"**商机类型**: {opportunity.opportunity_type}")
        content.append(f"**商机编号**: {opportunity.opportunity_number}")
        content.append(f"**销售流程**: {opportunity.sales_process}")
        content.append(f"**机会来源**: {opportunity.opportunity_source}")
        content.append(f"**生命状态**: {opportunity.lifecycle_status}")
        
        # Add parent opportunity information
        if opportunity.parent_opportunity:
            content.append(f"**上级商机**: {opportunity.parent_opportunity}")
        if opportunity.parent_opportunity_id:
            content.append(f"**上级商机ID**: {opportunity.parent_opportunity_id}")
            
        # Add opportunity financial information
        content.append(f"\n## 财务信息")
        content.append(f"**预估TCV**: {opportunity.estimated_tcv}")
        content.append(f"**预估ACV**: {opportunity.estimated_acv}")
        content.append(f"**商机下单金额**: {opportunity.opportunity_order_amount}")
        
        if any([opportunity.license_amount, opportunity.ma_amount, 
                opportunity.service_days_amount, opportunity.product_subscription_amount]):
            content.append(f"\n### 金额明细")
            if opportunity.license_amount:
                content.append(f"**License金额**: {opportunity.license_amount}")
            if opportunity.ma_amount:
                content.append(f"**MA金额**: {opportunity.ma_amount}")
            if opportunity.service_days_amount:
                content.append(f"**人天服务金额**: {opportunity.service_days_amount}")
            if opportunity.product_subscription_amount:
                content.append(f"**产品订阅金额**: {opportunity.product_subscription_amount}")
        
        if any([opportunity.license_acv, opportunity.ma_acv, 
                opportunity.service_acv, opportunity.subscription_acv]):
            content.append(f"\n### ACV明细")
            if opportunity.license_acv:
                content.append(f"**License ACV**: {opportunity.license_acv}")
            if opportunity.ma_acv:
                content.append(f"**MA ACV**: {opportunity.ma_acv}")
            if opportunity.service_acv:
                content.append(f"**Service ACV**: {opportunity.service_acv}")
            if opportunity.subscription_acv:
                content.append(f"**Subscription ACV**: {opportunity.subscription_acv}")
        
        # Add income forecast information
        if any([opportunity.current_year_license_forecast, opportunity.current_year_ma_forecast,
                opportunity.current_year_service_forecast, opportunity.current_year_subscription_forecast]):
            content.append(f"\n### 当财年收入预测")
            content.append(f"**当财年总收入预测(不含税)**: {opportunity.current_year_revenue_forecast}")
            if opportunity.current_year_license_forecast:
                content.append(f"**当财年License收入预测(不含税)**: {opportunity.current_year_license_forecast}")
            if opportunity.current_year_ma_forecast:
                content.append(f"**当财年MA收入预测(不含税)**: {opportunity.current_year_ma_forecast}")
            if opportunity.current_year_service_forecast:
                content.append(f"**当财年Service收入预测(不含税)**: {opportunity.current_year_service_forecast}")
            if opportunity.current_year_subscription_forecast:
                content.append(f"**当财年Subscription收入预测(不含税)**: {opportunity.current_year_subscription_forecast}")
        
        # Add service related information
        content.append(f"\n## 服务信息")
        if opportunity.expected_service_start_date:
            content.append(f"**预计服务开始日期**: {opportunity.expected_service_start_date}")
        if opportunity.expected_service_end_date:
            content.append(f"**预计服务结束日期**: {opportunity.expected_service_end_date}")
        if opportunity.expected_service_duration_months:
            content.append(f"**预计服务时长(月)**: {opportunity.expected_service_duration_months}")
        if opportunity.service_days_type:
            content.append(f"**人天服务类型**: {opportunity.service_days_type}")
        if opportunity.expected_launch_date:
            content.append(f"**预计上线日期**: {opportunity.expected_launch_date}")
        
        # Add technical information
        if any([opportunity.primary_database, opportunity.data_volume,
                opportunity.current_database_resource, opportunity.current_database_pain_points]):
            content.append(f"\n## 技术信息")
            if opportunity.primary_database:
                content.append(f"**主要数据库**: {opportunity.primary_database}")
                if opportunity.primary_database_percentage:
                    content.append(f"**主要数据库占比**: {opportunity.primary_database_percentage}%")
            if opportunity.data_volume:
                content.append(f"**数据量**: {opportunity.data_volume}")
            if opportunity.data_volume_tb_gb:
                content.append(f"**数据量(TB/GB)**: {opportunity.data_volume_tb_gb}")
            if opportunity.current_database_resource:
                content.append(f"**主数据库当前所用资源**: {opportunity.current_database_resource}")
            if opportunity.current_database_pain_points:
                content.append(f"**现用数据库痛点**: {opportunity.current_database_pain_points}")
        
        # Add solution information
        if any([opportunity.solution_1_name, opportunity.solution_2_name, 
                opportunity.has_isv_joint_solution, opportunity.estimated_total_project_nodes]):
            content.append(f"\n## 解决方案信息")
            
            if opportunity.estimated_total_project_nodes:
                content.append(f"**预估整体项目节点数/vCPU数**: {opportunity.estimated_total_project_nodes}")
            
            if opportunity.has_isv_joint_solution == '是':
                content.append(f"**包含ISV联合解决方案**: 是")
            
            if opportunity.solution_1_name:
                content.append(f"\n### 联合解决方案1")
                content.append(f"**解决方案名称**: {opportunity.solution_1_name}")
                content.append(f"**解决方案编号**: {opportunity.solution_1_number}")
                if opportunity.solution_1_certified_partner:
                    content.append(f"**认证合作伙伴**: {opportunity.solution_1_certified_partner}")
                if opportunity.estimated_solution_1_nodes:
                    content.append(f"**预估节点数/vCPU数**: {opportunity.estimated_solution_1_nodes}")
                if opportunity.estimated_solution_1_percentage:
                    content.append(f"**占比**: {opportunity.estimated_solution_1_percentage}%")
                
            if opportunity.solution_2_name:
                content.append(f"\n### 联合解决方案2")
                content.append(f"**解决方案名称**: {opportunity.solution_2_name}")
                content.append(f"**解决方案编号**: {opportunity.solution_2_number}")
                if opportunity.solution_2_certified_partner:
                    content.append(f"**认证合作伙伴**: {opportunity.solution_2_certified_partner}")
                if opportunity.estimated_solution_2_nodes:
                    content.append(f"**预估节点数/vCPU数**: {opportunity.estimated_solution_2_nodes}")
                if opportunity.estimated_solution_2_percentage:
                    content.append(f"**占比**: {opportunity.estimated_solution_2_percentage}%")
            
            if opportunity.mutual_certified_solution_partner:
                content.append(f"\n### 互认证解决方案")
                content.append(f"**互认证合作伙伴**: {opportunity.mutual_certified_solution_partner}")
                if opportunity.mutual_certified_solution_percentage:
                    content.append(f"**占比**: {opportunity.mutual_certified_solution_percentage}%")
        
        # Add competition information
        if opportunity.competitor_name or opportunity.competitor_info or opportunity.competitor_advantages:
            content.append(f"\n## 竞争情况")
            if opportunity.competitor_name:
                content.append(f"**竞争对手**: {opportunity.competitor_name}")
            if opportunity.competitor_info:
                content.append(f"**竞争对手信息**: {opportunity.competitor_info}")
            if opportunity.competitor_advantages:
                content.append(f"**竞争对手优势**: {opportunity.competitor_advantages}")
        
        # Add risk assessment information
        content.append(f"\n## 风险与状态评估")
        if opportunity.customer_journey_percentage:
            content.append(f"**客户旅程**: {opportunity.customer_journey_percentage}%")
        if opportunity.timing_risk:
            content.append(f"**Timing风险**: {opportunity.timing_risk}")
        if opportunity.operational_risk_assessment:
            content.append(f"**运营判断风险**: {opportunity.operational_risk_assessment}")
        if opportunity.is_key_deal:
            content.append(f"**是否Key Deal**: {opportunity.is_key_deal}")
        if opportunity.is_slip_deal:
            content.append(f"**是否Slip Deal**: {opportunity.is_slip_deal}")
        if opportunity.forecast_type:
            content.append(f"**预测类型**: {opportunity.forecast_type}")
        if opportunity.lock_status:
            content.append(f"**锁定状态**: {opportunity.lock_status}")
        
        # Add partner information
        if any([opportunity.partner, opportunity.distributor, opportunity.alternative_distributor,
                opportunity.cloud_vendor, opportunity.partner_cooperation_mode]):
            content.append(f"\n## 合作伙伴信息")
            if opportunity.partner:
                content.append(f"**合作伙伴**: {opportunity.partner}")
            if opportunity.distributor:
                content.append(f"**经销商**: {opportunity.distributor}")
            if opportunity.alternative_distributor:
                content.append(f"**备选经销商**: {opportunity.alternative_distributor}")
            if opportunity.cloud_vendor:
                content.append(f"**Cloud厂商**: {opportunity.cloud_vendor}")
            if opportunity.partner_cooperation_mode:
                content.append(f"**合作模式**: {opportunity.partner_cooperation_mode}")
            if opportunity.general_agent:
                content.append(f"**总代理**: {opportunity.general_agent}")
            if opportunity.application_developer:
                content.append(f"**应用软件开发商**: {opportunity.application_developer}")
        
        # Add channel filing information
        if opportunity.is_channel_filing_opportunity == '是':
            content.append(f"\n## 渠道报备信息")
            content.append(f"**是否渠道报备商机**: 是")
            if opportunity.partner_opportunity_filing_id:
                content.append(f"**合作伙伴商机报备ID**: {opportunity.partner_opportunity_filing_id}")
            if opportunity.filing_partner_name:
                content.append(f"**报备合作伙伴名称**: {opportunity.filing_partner_name}")
            if opportunity.partner_filing_opportunity_owner:
                content.append(f"**合作伙伴报备商机负责人**: {opportunity.partner_filing_opportunity_owner}")
            if opportunity.is_double_credit:
                content.append(f"**是否Double Credit**: {opportunity.is_double_credit}")
        
        # Add opportunity detailed information
        if opportunity.sales_log_details:
            content.append(f"\n## 销售日志详情")
            content.append(opportunity.sales_log_details)
        
        if opportunity.call_high_notes:
            content.append(f"\n## Call High情况")
            content.append(opportunity.call_high_notes)
        
        if opportunity.customer_budget_status:
            content.append(f"\n## 客户预算情况")
            content.append(opportunity.customer_budget_status)
        
        if opportunity.todo_and_followup:
            content.append(f"\n## TODO与跟进事项")
            content.append(opportunity.todo_and_followup)
        
        if opportunity.timeline_project_progress:
            content.append(f"\n## 倒排时间表项目进展")
            content.append(opportunity.timeline_project_progress)
        
        if opportunity.countdown_next_plan:
            content.append(f"\n## 下一步计划(Countdown)")
            content.append(opportunity.countdown_next_plan)
        
        if opportunity.countdown_bottleneck:
            content.append(f"\n## 问题卡点(Countdown)")
            content.append(opportunity.countdown_bottleneck)
        
        # Add project process information
        if any([opportunity.bidding_method, opportunity.signing_type, 
                opportunity.contract_signing_status, opportunity.quotation_status, 
                opportunity.has_poc, opportunity.has_bid, opportunity.bid_result]):
            content.append(f"\n## 项目流程信息")
            if opportunity.bidding_method:
                content.append(f"**招标方式**: {opportunity.bidding_method}")
            if opportunity.signing_type:
                content.append(f"**签约类型**: {opportunity.signing_type}")
            if opportunity.contract_signing_status:
                content.append(f"**合同签署状态**: {opportunity.contract_signing_status}")
            if opportunity.quotation_status:
                content.append(f"**报价单状态**: {opportunity.quotation_status}")
            if opportunity.order_status:
                content.append(f"**订单状态**: {opportunity.order_status}")
            if opportunity.project_approval:
                content.append(f"**立项批复**: {opportunity.project_approval}")
            if opportunity.has_poc:
                content.append(f"**是否进行PoC**: {opportunity.has_poc}")
            if opportunity.has_bid:
                content.append(f"**是否投标**: {opportunity.has_bid}")
            if opportunity.bid_result:
                content.append(f"**中标结果**: {opportunity.bid_result}")
            if opportunity.loss_reason:
                content.append(f"**丢单原因**: {opportunity.loss_reason}")
        
        # Add customer information
        if account:
            content.extend(format_account_info(account))            
            # Add former name into account info
            if opportunity.former_name:
                content.append(f"**曾用名**: {opportunity.former_name}")
                
        # Add contact information
        if contacts:
            content.extend(format_contacts_info(contacts))
            
        # Add recent update records
        if updates:
            content.extend(format_opportunity_updates(updates))

        # Add opportunity remarks
        if opportunity.remarks:
            content.append(f"\n## 备注")
            content.append(opportunity.remarks)
        
        # Add creation and modification information
        content.append(f"\n## 系统信息")
        if opportunity.creator:
            content.append(f"**创建人**: {opportunity.creator}")
        if opportunity.create_time:
            content.append(f"**创建时间**: {opportunity.create_time}")
        if opportunity.last_modifier:
            content.append(f"**最后修改人**: {opportunity.last_modifier}")
        if opportunity.last_modified_time:
            content.append(f"**最后修改时间**: {opportunity.last_modified_time}")
        if opportunity.last_followup_time:
            content.append(f"**最后跟进时间**: {opportunity.last_followup_time}")
        
        return "\n".join(content)
    
    def _create_metadata(self, opportunity, account, contacts, updates=None) -> Dict:
        """Create document metadata, including rich opportunity, customer and contact information."""
        
        metadata = {"category": DocumentCategory.CRM}
    
        # 动态处理商机信息
        if opportunity:
            _, opportunity_columns = get_column_comments_and_names(type(opportunity))
            
            # 需要排除的字段
            exclude_fields = {"id"}
                        
            # 处理商机基本字段
            for field_name in opportunity_columns:
                if field_name in exclude_fields:
                    continue
                    
                value = getattr(opportunity, field_name, None)
                if value is None:
                    continue
                    
                # 特殊处理需要转换为浮点数的字段
                if field_name in {"forecast_amount", "estimated_tcv", "estimated_acv", 
                                "opportunity_order_amount", "customer_journey_percentage"}:
                    try:
                        metadata[field_name] = float(value) if value else 0.0
                    except (ValueError, TypeError):
                        metadata[field_name] = 0.0
                
                # 特殊处理日期时间字段
                elif field_name in {"expected_closing_date", "create_time", "last_modified_time", 
                                "last_followup_time", "expected_launch_date"}:
                    if isinstance(value, (datetime, date)):
                        metadata[field_name] = value.isoformat()
                    else:
                        metadata[field_name] = str(value)
                
                # 处理标志性字段，转换为布尔值
                elif field_name in {"is_slip_deal", "is_channel_filing_opportunity", "has_isv_joint_solution"}:
                    # 转换为更符合元数据命名习惯的键名
                    # if field_name == "is_channel_filing_opportunity":
                    #     metadata["is_partner_opportunity"] = True
                    # elif field_name == "has_isv_joint_solution":
                    #     metadata["has_isv_solution"] = True
                    # else:
                    metadata[field_name] = True if value == '是' else False
                
                # 常规字段处理
                else:
                    metadata[field_name] = str(value) if value else ""
        
        # 动态处理客户信息
        if account:
            _, account_columns = get_column_comments_and_names(type(account))
            
            # 创建account嵌套对象
            account_data = {}
                        
            # 客户信息中需要包含的重要字段
            important_fields = {
                "unique_id", "customer_name", "industry", "customer_level",
                "annual_revenue", "number_of_employees"
            }
            
            for field_name in important_fields:
                if field_name not in account_columns:
                    continue
                    
                value = getattr(account, field_name, None)
                if value is None:
                    continue
               
                # 使用标准化字段名添加到account嵌套对象中
                if field_name == "unique_id":
                    account_data["account_id"] = str(value) if value else ""
                elif field_name == "customer_name":
                    account_data["account_name"] = str(value) if value else ""
                else:
                    account_data[field_name] = str(value) if value else ""
            
            # 将整个account数据作为单独字段添加到metadata
            metadata["account"] = account_data
                
        # 处理解决方案信息
        if opportunity and hasattr(opportunity, "solution_1_name") and opportunity.solution_1_name:
            metadata["solution_1_name"] = opportunity.solution_1_name
            if hasattr(opportunity, "solution_1_certified_partner") and opportunity.solution_1_certified_partner:
                metadata["solution_1_partner"] = opportunity.solution_1_certified_partner
        
        if opportunity and hasattr(opportunity, "solution_2_name") and opportunity.solution_2_name:
            metadata["solution_2_name"] = opportunity.solution_2_name
            if hasattr(opportunity, "solution_2_certified_partner") and opportunity.solution_2_certified_partner:
                metadata["solution_2_partner"] = opportunity.solution_2_certified_partner
        
        # 处理合作伙伴信息
        if opportunity and hasattr(opportunity, "filing_partner_name") and opportunity.filing_partner_name:
            metadata["partner_name"] = opportunity.filing_partner_name
        
        # 处理联系人信息
        if contacts:
            # 关键决策者
            key_contacts = [c for c in contacts if hasattr(c, 'key_decision_maker') and getattr(c, 'key_decision_maker', '') == '是']
            if key_contacts:
                metadata["key_contacts"] = [contact.name for contact in key_contacts]
                metadata["has_key_decision_maker"] = True
            else:
                metadata["has_key_decision_maker"] = False
            
            # 联系人数量
            metadata["contact_count"] = len(contacts)
                   
            # 添加完整的联系人信息
            contacts_data = []
            for contact in contacts:
                contact_info = {
                    "contact_id": contact.unique_id if hasattr(contact, 'unique_id') else "",
                    "contact_name": contact.name if hasattr(contact, 'name') else "",
                    "position": (
                        contact.position if hasattr(contact, 'position') and contact.position 
                        else (contact.position1 if hasattr(contact, 'position1') and contact.position1 else "")
                    ),
                    "key_decision_maker": getattr(contact, 'key_decision_maker', '') == '是',
                     "department": (
                        contact.department if hasattr(contact, 'department') and contact.department 
                        else (contact.department1 if hasattr(contact, 'department1') and contact.department1 else "")
                    ),
                    "direct_superior_id": contact.direct_superior_id if hasattr(contact, 'direct_superior_id') else "",
                    "direct_superior": contact.direct_superior if hasattr(contact, 'direct_superior') else "",
                    "influence_level": contact.influence_level if hasattr(contact, 'influence_level') else "",
                    "relationship_strength": contact.relationship_strength if hasattr(contact, 'relationship_strength') else "",
                    "account_id": contact.customer_id if hasattr(contact, 'customer_id') else "",
                }
                contacts_data.append(contact_info)
                        
            metadata["contacts"] = contacts_data
            
        # 处理更新记录信息
        if updates and len(updates) > 0:
            metadata["has_recent_updates"] = True
            metadata["update_count"] = len(updates)
            
            # 获取最新更新
            # 通过更新日期排序
            sorted_updates = sorted(updates, key=lambda x: getattr(x, 'update_date', datetime.min), reverse=True)
            if sorted_updates:
                latest_update = sorted_updates[0]  # 最新的更新
                
                if hasattr(latest_update, 'record_date'):
                    if isinstance(latest_update.record_date, date):
                        metadata["latest_update_date"] = latest_update.record_date.isoformat()
                    else:
                        metadata["latest_update_date"] = str(latest_update.record_date)
                
                if hasattr(latest_update, 'update_type'):
                    metadata["latest_update_type"] = latest_update.update_type
                
                if hasattr(latest_update, 'customer_sentiment') and latest_update.customer_sentiment:
                    metadata["latest_customer_sentiment"] = latest_update.customer_sentiment
        else:
            metadata["has_recent_updates"] = False
        
        # 特殊标记
        if opportunity and hasattr(opportunity, 'loss_reason') and opportunity.loss_reason:
            metadata["lost_opportunity"] = True
            metadata["loss_reason"] = opportunity.loss_reason
        
        # 确保所有值都是JSON可序列化的
        for key, value in list(metadata.items()):
            if isinstance(value, (datetime, date)):
                metadata[key] = value.isoformat()
            elif not (isinstance(value, (str, int, float, bool, list, dict)) or value is None):
                metadata[key] = str(value)
        
        return metadata