from datetime import date, datetime
from typing import Any, List
import logging

logger = logging.getLogger(__name__)
    
def get_column_comments_and_names(model_class) -> tuple:
    """获取模型的列注释和所有列名"""
    comments_as_display_names = {}
    column_names = set()
 
    # 尝试使用 SQLModel 模型的 schema 属性
    if hasattr(model_class, 'schema') and model_class.schema:
        for field_name, field_info in model_class.schema()['properties'].items():
            if 'description' in field_info:
                comments_as_display_names[field_name] = field_info['description']
                column_names.add(field_name)
    
    logger.info(f"Found {len(column_names)} columns with {len(comments_as_display_names)} comments for Model: {model_class.__name__}")
    
    return comments_as_display_names, column_names


# 客户信息处理函数
def format_account_info(account) -> List[str]:
    """动态处理客户信息，仅使用模型中定义的字段"""
    if not account:
        return []
    
    content = []
    account_name = getattr(account, "customer_name", None) or "未命名客户"
    content.append(f"# 客户：{account_name}")
        
    # 获取客户模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(account))
    
    # 特殊处理的字段组（根据实际需要调整分组显示）
    contact_fields = {"phone", "website", "email"}
    date_fields = {"last_follow_up", "last_deal_time", "allocation_time", "creation_time", 
                  "last_modification_time", "earliest_deal_date", "latest_deal_date"}
    status_fields = {"allocation_status", "deal_status", "life_status"}
    
    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "customer_name"}
    
    # 优先显示的核心字段
    priority_fields = {"customer_level", "industry", "customer_source", "business_type", "customer_attribute"}
    
    # 先处理优先字段
    for field_name in priority_fields:
        if field_name not in valid_columns:
            continue
            
        value = getattr(account, field_name)
        if value is None:
            continue
        
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        content.append(f"**{display_name}**: {value}")
    
    # 处理联系人信息
    contact_info = []
    for field_name in contact_fields:
        if field_name not in valid_columns:
            continue
            
        value = getattr(account, field_name)
        if value is None or value == "":
            continue
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        contact_info.append(f"**{display_name}**: {value}")
    
    if contact_info:
        content.append("\n## 联系方式")
        content.extend(contact_info)
    
    # 处理日期字段
    date_info = []
    for field_name in date_fields:
        if field_name not in valid_columns:
            continue
            
        value = getattr(account, field_name)
        if value is None:
            continue
            
        # 格式化日期
        formatted_date = value
        if isinstance(value, (datetime, date)):
            formatted_date = value.strftime("%Y-%m-%d")
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        date_info.append(f"**{display_name}**: {formatted_date}")
    
    if date_info:
        content.append("\n## 时间信息")
        content.extend(date_info)
    
    # 处理状态字段
    status_info = []
    for field_name in status_fields:
        if field_name not in valid_columns:
            continue
            
        value = getattr(account, field_name)
        if value is None:
            continue
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        status_info.append(f"**{display_name}**: {value}")
    
    if status_info:
        content.append("\n## 状态信息")
        content.extend(status_info)
    
    # 处理备注信息
    if "remarks" in valid_columns and account.remarks:
        remarks_label = column_comments.get("remarks", "备注")
        content.append(f"\n## {remarks_label}")
        content.append(account.remarks)
    
    # 处理其他未分类字段
    special_fields = contact_fields.union(date_fields).union(status_fields)
    special_fields.add("remarks")
    special_fields = special_fields.union(priority_fields)
    
    for field_name in valid_columns:
        # 跳过已排除字段和特殊处理字段
        if field_name in exclude_fields or field_name in special_fields:
            continue
            
        value = getattr(account, field_name)
        # 跳过空值
        if value is None:
            continue
        
        # 使用列注释作为显示名称
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        content.append(f"**{display_name}**: {value}")
    
    return content

def format_contact_info(contact) -> List[str]:
    """动态处理联系人信息，仅使用模型中定义的字段"""
    if not contact:
        return []
    
    content = []
    contact_name = getattr(contact, 'name', 'None') or '未知联系人'
    content.append(f"# 联系人：{contact_name}")
        
    # 获取联系人模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(contact))
    
    # 分组字段
    identity_fields = {"position", "position1", "department", "department1", "key_decision_maker", 
                        "direct_superior", "direct_superior_id"}
    contact_method_fields = {"mobile1", "phone1", "email", "wechat", "address"}
    date_fields = {"birthday"}
    special_fields = {"gender"}
    
    # 需要排除的字段
    exclude_fields = {"id", "name", "customer_id", "unique_id", "direct_superior_id"}
    
    # 处理身份信息
    # 优先使用position/department，如果为空则使用position1/department1
    if "position" in valid_columns and getattr(contact, "position"):
        position_label = column_comments.get("position", "职务")
        content.append(f"**{position_label}**: {getattr(contact, 'position')}")
    elif "position1" in valid_columns and getattr(contact, "position1"):
        position_label = column_comments.get("position1", "职务1")
        content.append(f"**{position_label}**: {getattr(contact, 'position1')}")
        
    if "department" in valid_columns and getattr(contact, "department"):
        dept_label = column_comments.get("department", "部门")
        content.append(f"**{dept_label}**: {getattr(contact, 'department')}")
    elif "department1" in valid_columns and getattr(contact, "department1"):
        dept_label = column_comments.get("department1", "部门1")
        content.append(f"**{dept_label}**: {getattr(contact, 'department1')}")
    
    # 处理关键决策者
    if "key_decision_maker" in valid_columns and getattr(contact, "key_decision_maker"):
        kdm_value = getattr(contact, "key_decision_maker")
        kdm_label = column_comments.get("key_decision_maker", "关键决策人")
        kdm_formatted = '是' if str(kdm_value).lower() in ('是', 'yes', 'true', '1') else '否'
        content.append(f"**{kdm_label}**: {kdm_formatted}")
            
    # 处理直属上级信息
    if "direct_superior" in valid_columns and getattr(contact, "direct_superior"):
        superior_label = column_comments.get("direct_superior", "直属上级")
        content.append(f"**{superior_label}**: {getattr(contact, 'direct_superior')}")
        
    if "direct_superior_id" in valid_columns and getattr(contact, "direct_superior_id"):
        superior_id_label = column_comments.get("direct_superior_id", "直属上级ID")
        content.append(f"**{superior_id_label}**: {getattr(contact, 'direct_superior_id')}")
    
    
    # 处理联系方式
    contact_methods = []
    if 'mobile1' in valid_columns and getattr(contact, 'mobile1'):
        mobile_label = column_comments.get('mobile1', '手机')
        contact_methods.append(f"{mobile_label}: {contact.mobile1}")
    
    if 'phone1' in valid_columns and getattr(contact, 'phone1'):
        phone_label = column_comments.get('phone1', '电话')
        contact_methods.append(f"{phone_label}: {contact.phone1}")
    
    if "email" in valid_columns and getattr(contact, "email"):
        email_label = column_comments.get("email", "邮件")
        contact_methods.append(f"{email_label}: {contact.email}")
    
    if "wechat" in valid_columns and getattr(contact, "wechat"):
        wechat_label = column_comments.get("wechat", "微信")
        contact_methods.append(f"{wechat_label}: {contact.wechat}")
        
    if "address" in valid_columns and getattr(contact, "address"):
        address_label = column_comments.get("address", "联系地址")
        contact_methods.append(f"{address_label}: {contact.address}")
    
    if contact_methods:
        content.append(f"**联系方式**:")
        for method in contact_methods:
            content.append(f"- {method}")
            
    # 处理性别
    if "gender" in valid_columns and getattr(contact, "gender"):
        gender_label = column_comments.get("gender", "性别")
        content.append(f"**{gender_label}**: {getattr(contact, 'gender')}")
        
    # 处理日期字段
    for field_name in date_fields:
        if field_name not in valid_columns:
            continue
            
        value = getattr(contact, field_name)
        if value is None:
            continue
            
        # 格式化日期
        formatted_date = value
        if isinstance(value, (datetime, date)):
            formatted_date = value.strftime("%Y-%m-%d")
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        content.append(f"**{display_name}**: {formatted_date}")
        
    # 处理其他字段
    all_special_fields = identity_fields.union(contact_method_fields).union(date_fields).union(special_fields).union(exclude_fields)
    
    # 找出重要字段先显示
    important_fields = {"attitude", "status"}
    
    for field_name in important_fields:
        if field_name not in valid_columns or field_name in all_special_fields:
            continue
            
        value = getattr(contact, field_name)
        if value is None:
            continue
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        content.append(f"**{display_name}**: {value}")
        
    # 添加已处理的重要字段到特殊字段集合
    all_special_fields = all_special_fields.union(important_fields)
    
    # 处理其余所有字段
    for field_name in valid_columns:
        # 跳过已处理的字段
        if field_name in all_special_fields:
            continue
            
        value = getattr(contact, field_name)
        if value is None:
            continue
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        content.append(f"**{display_name}**: {value}")
    
    return content

def format_opportunity_info(opportunity) -> List[str]:
    """动态处理商机信息，仅使用模型中定义的字段"""
    if not opportunity:
        return []
        
    content = []
    opp_name = getattr(opportunity, "opportunity_name", None) or "未命名商机"
    content.append(f"# 商机：{opp_name}")
    
    # 获取商机模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(opportunity))
    
    field_groups = {
        "basic": {
            "title": "",  # 基本信息不需要标题
            "fields": ["customer_name", "customer_type", "customer_category", "owner", 
                    "opportunity_stage", "stage_status", "expected_closing_date", 
                    "expected_closing_quarter", "business_type", "opportunity_type", 
                    "opportunity_source", "customer_business_scenario"]
        },
        "financial": {
            "title": "## 财务信息",
            "fields": ["estimated_tcv", "estimated_acv"]
        },
        "forecast": {
            "title": "### 当财年收入预测",
            "fields": ["current_year_service_forecast"]
        },
        "competitor": {
            "title": "## 竞争情况",
            "fields": ["competitor_name"]
        },
        "risk": {
            "title": "## 风险与状态评估",
            "fields": ["forecast_type", "renew_risk_level"]
        },
        "partner": {
            "title": "## 合作伙伴信息",
            "fields": ["general_agent"]
        },
        "channel": {
            "title": "## 渠道报备信息",
            "fields": ["is_channel_reported_opportunity", "partner_opportunity_filing_id_unique_id", 
                    "partner_opportunity_filing_number", "filing_opportunity_number"]
        },
        "process": {
            "title": "## 项目流程信息",
            "fields": ["signing_type", "quotation_status", "is_project_approved", 
                    "project_date", "bidding_time", "budget_approval_status", "lost_reason"]
        },
        "status_tracking": {
            "title": "## 商机状态跟踪",
            "fields": ["weekly_update", "sl_pull_in", "quotation_order_status", 
                    "sales_order_archive_status", "latest_followup_date_new"]
        },
        "system": {
            "title": "## 系统信息",
            "fields": ["create_time", "last_modifier", "last_modified_time", "last_followup_time"]
        }
    }
        
    # 详细信息字段（长文本）单独处理
    detail_fields = {
        "call_high_notes": "Call High情况",
        "customer_budget_status": "客户预算情况",
        "todo_and_followup": "TODO与跟进事项",
        "remarks": "备注说明"
    }

    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "opportunity_name", "customer_id"}
     
    # 需要特殊处理的布尔字段 - 显示为"是/否"
    boolean_fields = {"is_channel_reported_opportunity", "is_project_approved"}
    
    # 需要特殊处理的金额字段 - 格式化显示
    currency_fields = {"estimated_tcv", "estimated_acv", "current_year_service_forecast"}
  
    # 辅助函数：处理字段值的格式化
    def format_field_value(field_name, value):
        # 处理布尔值
        if field_name in boolean_fields:
            if str(value).lower() in ('true', '1', 'yes', '是', 't', 'y'):
                return "是"
            elif str(value).lower() in ('false', '0', 'no', '否', 'f', 'n'):
                return "否"
            return value
            
        # 处理日期时间
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        elif isinstance(value, date):
            return value.strftime("%Y-%m-%d")
            
        # 处理货币金额
        if field_name in currency_fields and value is not None:
            try:
                # 尝试将值转换为数字并格式化
                num_value = float(value)
                # 如果是整数，不显示小数位
                if num_value.is_integer():
                    return f"{int(num_value):,}"
                # 否则保留两位小数
                return f"{num_value:,.2f}"
            except (ValueError, TypeError):
                pass
                
        # 默认返回原值
        return value
    
    # 辅助函数：处理一组字段
    def process_field_group(group_info, condition_field=None):
        # 检查条件字段
        if condition_field and (condition_field not in valid_columns or 
                               not getattr(opportunity, condition_field, None)):
            return []
            
        group_content = []
        has_fields = False
        
        for field_name in group_info["fields"]:
            if field_name not in valid_columns or field_name in exclude_fields:
                continue
                
            value = getattr(opportunity, field_name, None)
            if value is None:
                continue
                
            if not has_fields and group_info["title"]:
                group_content.append(group_info["title"])
                has_fields = True
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            formatted_value = format_field_value(field_name, value)
            group_content.append(f"**{display_name}**: {formatted_value}")
            
        return group_content
       
    # 处理基本信息
    content.extend(process_field_group(field_groups["basic"]))
    
    # 处理财务信息
    financial_content = []
    financial_content.extend(process_field_group(field_groups["financial"]))
        
    # 只有当至少有一个收入预测字段有值时才显示收入预测
    if any(getattr(opportunity, field, None) for field in field_groups["forecast"]["fields"] 
          if field in valid_columns):
        financial_content.extend(process_field_group(field_groups["forecast"]))
        
    # 只有当财务信息组有内容时，才将财务信息添加到主内容中
    if len(financial_content) > 0:
        content.extend(financial_content)
        
    # 处理竞争情况
    competitor_fields = field_groups["competitor"]["fields"]
    if any(getattr(opportunity, field, None) for field in competitor_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["competitor"]))
    
    # 处理风险评估
    risk_fields = field_groups["risk"]["fields"]
    if any(getattr(opportunity, field, None) for field in risk_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["risk"]))
    
    # 处理合作伙伴信息
    partner_fields = field_groups["partner"]["fields"]
    if any(getattr(opportunity, field, None) for field in partner_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["partner"]))
    
    # 处理渠道报备信息 - 只有当is_channel_reported_opportunity为"是"时才显示
    if "is_channel_reported_opportunity" in valid_columns and getattr(opportunity, "is_channel_reported_opportunity", None) in ('是', 'True', 'true', 'YES', 'yes', '1'):
       content.extend(process_field_group(field_groups["channel"], "is_channel_reported_opportunity"))
       
    # 处理项目流程信息
    process_fields = field_groups["process"]["fields"]
    if any(getattr(opportunity, field, None) for field in process_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["process"]))
    
    # 处理商机状态跟踪信息
    status_fields = field_groups["status_tracking"]["fields"]
    if any(getattr(opportunity, field, None) for field in status_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["status_tracking"]))
    
    # 处理详细信息字段（长文本）
    for field_name, display_title in detail_fields.items():
        if field_name not in valid_columns:
            continue
            
        value = getattr(opportunity, field_name, None)
        if value is None or value == "":
            continue
        
        title = column_comments.get(field_name, display_title)
        content.append(f"\n## {title}")
        content.append(value)
    
    # 处理系统信息
    content.extend(process_field_group(field_groups["system"]))
    
    return content

def format_opportunity_updates(updates: List[Any]) -> List[str]:
    """动态处理商机更新记录，仅使用CRMOpportunityUpdate模型中定义的字段"""
    if not updates:
        return []
        
    content = []
    content.append(f"\n## 商机更新记录")
    
    # 按更新日期倒序排序，显示最新的更新记录在前
    sorted_updates = sorted(updates, key=lambda x: getattr(x, 'update_date', datetime.min), reverse=True)
    
    for update in sorted_updates:
        # 获取模型的列注释和字段名
        column_comments, valid_columns = get_column_comments_and_names(type(update))
        
        # 获取更新的日期和类型，作为小节标题
        update_date = getattr(update, 'update_date', None)
        update_type = getattr(update, 'update_type', '更新记录')
        
        date_str = update_date.strftime("%Y-%m-%d %H:%M") if isinstance(update_date, datetime) else "未知日期"
        content.append(f"\n### {date_str} - {update_type}")
        
        # 处理摘要信息 - 优先显示
        if 'summary' in valid_columns and getattr(update, 'summary'):
            summary_label = column_comments.get('summary', '更新摘要')
            content.append(f"**{summary_label}**: {getattr(update, 'summary')}")
        
        # 处理创建者信息
        if 'creator' in valid_columns and getattr(update, 'creator'):
            creator_label = column_comments.get('creator', '创建人')
            content.append(f"**{creator_label}**: {getattr(update, 'creator')}")
        
        # 处理客户态度和成单概率变化
        sentiment_fields = {'customer_sentiment', 'deal_probability_change'}
        for field_name in sentiment_fields:
            if field_name in valid_columns and getattr(update, field_name):
                display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
                content.append(f"**{display_name}**: {getattr(update, field_name)}")
        
        # 处理详细描述 - 作为独立段落显示
        if 'detailed_notes' in valid_columns and getattr(update, 'detailed_notes'):
            notes_label = column_comments.get('detailed_notes', '详细描述和进展')
            content.append(f"\n**{notes_label}**:")
            content.append(f"{getattr(update, 'detailed_notes')}")
        
        # 处理下一步计划
        if 'next_steps' in valid_columns and getattr(update, 'next_steps'):
            steps_label = column_comments.get('next_steps', '下一步行动计划')
            content.append(f"\n**{steps_label}**:")
            content.append(f"{getattr(update, 'next_steps')}")
        
        # 处理关键干系人
        if 'key_stakeholders' in valid_columns and getattr(update, 'key_stakeholders'):
            stakeholders_label = column_comments.get('key_stakeholders', '相关关键干系人')
            content.append(f"\n**{stakeholders_label}**:")
            content.append(f"{getattr(update, 'key_stakeholders')}")
        
        # 处理障碍/挑战
        if 'blockers' in valid_columns and getattr(update, 'blockers'):
            blockers_label = column_comments.get('blockers', '当前障碍或挑战')
            content.append(f"\n**{blockers_label}**:")
            content.append(f"{getattr(update, 'blockers')}")
        
        # 已处理的字段集合
        processed_fields = {
            'id', 'opportunity_id', 'record_date', 'update_type', 'update_date', 
            'creator', 'creator_id', 'summary', 'detailed_notes', 'next_steps',
            'key_stakeholders', 'customer_sentiment', 'deal_probability_change', 
            'blockers', 'create_time', 'last_modified_time'
        }
        
        # 处理剩余字段（如果有模型更新增加了新字段）
        for field_name in valid_columns:
            # 跳过已处理字段
            if field_name in processed_fields:
                continue
                
            value = getattr(update, field_name)
            if value is None:
                continue
            
            # 格式化日期和时间
            if isinstance(value, (datetime, date)):
                value = value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else value.strftime("%Y-%m-%d")
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            content.append(f"**{display_name}**: {value}")
    
    return content