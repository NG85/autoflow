from datetime import date, datetime
from typing import Any, List
import logging

logger = logging.getLogger(__name__)
    
def get_column_comments_and_names(model_class, filter_text: bool = False, filter_max_length: int = 300) -> tuple:
    """获取模型的列注释和所有列名"""
    comments_as_display_names = {}
    column_names = set()
    excluded_fields = set()
  
    # 如果需要过滤长文本
    if filter_text:
        # 检查模型的所有字段
        for field_name in dir(model_class):
            field = getattr(model_class, field_name, None)
            
            # 跳过非字段属性和特殊属性
            if field_name.startswith('_') or not hasattr(field, 'sa_column'):
                continue
            
            sa_column = getattr(field, 'sa_column', None)
            if sa_column is not None:
                # 检查是否为Text类型
                if sa_column.type.__class__.__name__ == "Text":
                    excluded_fields.add(field_name)
                    logger.debug(f"Excluding Text field: {field_name}")
                    continue
                
                # 检查VARCHAR类型的长度限制
                if hasattr(sa_column.type, 'length'):
                    # 对于长度大于300的VARCHAR字段，也视为长文本字段
                    if sa_column.type.length and sa_column.type.length > filter_max_length:
                        excluded_fields.add(field_name)
                        logger.debug(f"Excluding long VARCHAR field: {field_name} (length: {sa_column.type.length})")
                        continue
 
    # 尝试使用 SQLModel 模型的 schema 属性
    if hasattr(model_class, 'schema') and model_class.schema:
        for field_name, field_info in model_class.schema()['properties'].items():
            # 跳过已被标记为排除的字段
            if field_name in excluded_fields:
                continue
            
            if 'description' in field_info:
                comments_as_display_names[field_name] = field_info['description']
                column_names.add(field_name)
    
    logger.info(f"Found {len(column_names)} columns with {len(comments_as_display_names)} comments for Model: {model_class.__name__}")
    if filter_text:
        logger.info(f"Excluded {len(excluded_fields)} long text fields")
    
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
                  "last_modified_time", "earliest_deal_date", "latest_deal_date"}
    status_fields = {"allocation_status", "deal_status", "life_status"}
    
    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "customer_name"}
    
    # 优先显示的核心字段
    priority_fields = {"customer_level", "industry", "customer_source", "business_type", "customer_attribute"}
    
    # 我方负责人信息
    responsible_fields = {"person_in_charge", "department"}
    
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
    special_fields = special_fields.union(priority_fields).union(responsible_fields)
    
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
    
    # 单独处理我方负责人信息
    responsible_info = []
    if "person_in_charge" in valid_columns and getattr(account, "person_in_charge"):
        responsible_info.append(f"**姓名**: {getattr(account, 'person_in_charge')}")
    
    if "department" in valid_columns and getattr(account, "department"):
        responsible_info.append(f"**主属部门**: {getattr(account, 'department')}")
    
    if responsible_info:
        content.append("\n# 我方对接人信息")
        content.extend(responsible_info)
        content.append("**说明**：以上“对接人”为我方（公司内部）人员，非客户方。")

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
    
    # 我方负责人信息
    responsible_fields = {"responsible_person", "responsible_department"}
    
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
    all_special_fields = identity_fields.union(contact_method_fields).union(date_fields).union(special_fields).union(exclude_fields).union(responsible_fields)
    
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
    
    # 单独处理我方对接负责人信息
    responsible_info = []
    if "responsible_person" in valid_columns and getattr(contact, "responsible_person"):
        responsible_info.append(f"**姓名**: {getattr(contact, 'responsible_person')}")
    
    if "responsible_department" in valid_columns and getattr(contact, "responsible_department"):
        responsible_info.append(f"**主属部门**: {getattr(contact, 'responsible_department')}")
    
    if responsible_info:
        content.append("---")
        content.append("\n# 我方对接人信息")
        content.extend(responsible_info)
        content.append("**说明**：以上“对接人”为我方（公司内部）人员，非客户方。")

    return content

def format_opportunity_info(opportunity, related_data) -> List[str]:
    """动态处理商机信息，仅使用模型中定义的字段"""
    if not opportunity:
        return []
        
    content = []
    opp_name = getattr(opportunity, "opportunity_name", None) or "未命名商机"
    content.append(f"# 商机：{opp_name}\n")
    
    # 获取商机模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(opportunity))
    
    field_groups = {
        "basic": {
            "title": "",  # 基本信息不需要标题
            "fields": ["customer_name", "customer_type", "customer_category", 
                "opportunity_stage", "stage_status", "business_type", "opportunity_type", "opportunity_source",
                "customer_business_scenario", "expected_closing_date", "expected_closing_quarter"]
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
            "fields": [# "weekly_update",
                "sl_pull_in", "quotation_order_status", 
                "sales_order_archive_status", #"latest_followup_date_new",
                "last_followup_time"]
        },
        "owner": {
            "title": "## 负责人信息",
            "fields": ["presales_owner", "owner", "owner_main_department"]
        },
        "system": {
            "title": "## 系统信息",
            "fields": ["create_time", "last_modifier", "last_modified_time"]
        }
    }
        
    # 详细信息字段（长文本）单独处理
    detail_fields = {
        # "call_high_notes": "Call High情况",
        "customer_budget_status": "客户预算情况",
        # "todo_and_followup": "TODO与跟进事项",
        "remarks": "备注说明"
    }

    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "opportunity_name", "customer_id"}
     
    # 需要特殊处理的布尔字段 - 显示为"是/否"
    boolean_fields = {"is_channel_reported_opportunity", "is_project_approved"}
    
    # 需要特殊处理的金额字段 - 格式化显示
    currency_fields = {"estimated_tcv", "estimated_acv", "current_year_service_forecast"}
    
    # 需要特殊处理的JSON样式文本字段
    json_fields = {"owner", "owner_main_department", "presales_owner", "last_modifier"}
  
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
         
        # 处理JSON格式文本字段
        if field_name in json_fields and value:
            parsed_value = value
            if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                try:
                    # 尝试解析JSON格式的字符串
                    import json
                    parsed_value = json.loads(value)
                except:
                    pass
            
            if isinstance(parsed_value, list):
                return ','.join(parsed_value)
            else:
                return str(parsed_value)
        
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
                value = "暂无"
                
            if not has_fields and group_info["title"]:
                group_content.append(group_info["title"])
                has_fields = True
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            formatted_value = format_field_value(field_name, value)
            group_content.append(f"- {display_name}: {formatted_value}\n")
            
        return group_content
       
    # 处理基本信息
    content.extend(process_field_group(field_groups["basic"]))
    content.append("> 说明：季度格式为'FY25Q1'，表示财年2025第一季度。财年从自然年4月1日开始计算，例如2024年4月1日开始的季度为FY25Q1。\n")
    
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
    competitor_content = process_field_group(field_groups["competitor"])
    if competitor_content:
        content.extend(competitor_content)
    
    # 处理风险评估
    risk_content = process_field_group(field_groups["risk"])
    if risk_content:
        content.extend(risk_content)
    
    # 处理合作伙伴信息
    partner_content = process_field_group(field_groups["partner"])
    if partner_content:
        content.extend(partner_content)
    
    # 处理渠道报备信息
    channel_content = process_field_group(field_groups["channel"])
    if channel_content:
        content.extend(channel_content)
       
    # 处理项目流程信息
    process_content = process_field_group(field_groups["process"])
    if process_content:
        content.extend(process_content)
    
    # 处理商机状态跟踪信息
    status_fields = field_groups["status_tracking"]["fields"]
    if any(getattr(opportunity, field, None) for field in status_fields if field in valid_columns):
        content.extend(process_field_group(field_groups["status_tracking"]))
        content.append("> 说明：以上【跟进时间】为商机完成一些阶段任务，项目有跟进动作，例如阶段推进、增加关联的销售记录等的时间。\n")
    
    # 处理详细信息字段（长文本）
    for field_name, display_title in detail_fields.items():
        if field_name not in valid_columns:
            continue
            
        value = getattr(opportunity, field_name, None)
        if value is None:
            value = "暂无"
        
        title = column_comments.get(field_name, display_title)
        content.append(f"\n## {title}")
        content.append(value)
    
    # 处理负责人信息
    content.extend(process_field_group(field_groups["owner"]))

    # 处理系统信息
    content.extend(process_field_group(field_groups["system"]))
    content.append("> 说明：以上【修改时间】为修改商机任何字段的具体数值的时间\n")
    
    # 处理订单信息
    if related_data and "orders_with_payment_plans" in related_data:
        for order_with_payment_plans in related_data["orders_with_payment_plans"]:
            content.extend(format_order_info(order_with_payment_plans["order"]))
            if order_with_payment_plans["payment_plans"]:
                for payment_plan in order_with_payment_plans["payment_plans"]:
                    content.extend(format_payment_plan_info(payment_plan))
    
    # 处理商机更新记录
    if related_data and "opportunity_updates" in related_data:
        content.extend(format_opportunity_updates(related_data["opportunity_updates"], opportunity.opportunity_name))
    
    return content

def format_opportunity_updates(updates, opportunity_name: str) -> List[str]:
    """动态处理商机更新记录，仅使用CRMOpportunityUpdates模型中定义的字段"""
    if not updates:
        return []
        
    content = []
    
    for update in updates:
        # 获取模型的列注释和字段名
        column_comments, valid_columns = get_column_comments_and_names(type(update))
        
        # 获取记录的日期和类型，作为小节标题
        record_date = getattr(update, 'record_date', None)
        update_type = getattr(update, 'update_type', '未知分类')
        
        date_str = record_date.strftime("%Y-%m-%d") if isinstance(record_date, date) else "未知日期"
        content.append(f"\n## {date_str} {update_type}类的销售活动")
        
        # 处理摘要信息 - 优先显示
        if 'summary' in valid_columns and getattr(update, 'summary'):
            summary_label = column_comments.get('summary', '更新摘要')
            content.append(f"**{summary_label}**: {getattr(update, 'summary')}\n")
        
        # 处理创建者信息
        if 'creator' in valid_columns and getattr(update, 'creator'):
            creator_label = column_comments.get('creator', '创建人')
            content.append(f"**{creator_label}**: {getattr(update, 'creator')}\n")
        
        # 处理客户态度和成单概率变化
        sentiment_fields = {'customer_sentiment', 'deal_probability_change'}
        for field_name in sentiment_fields:
            if field_name in valid_columns and getattr(update, field_name):
                display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
                content.append(f"**{display_name}**: {getattr(update, field_name)}\n")
        
        # 处理详细描述
        if 'detailed_notes' in valid_columns and getattr(update, 'detailed_notes'):
            notes_label = column_comments.get('detailed_notes', '详细描述和进展')
            content.append(f"\n### {notes_label}:")
            content.append(f"{getattr(update, 'detailed_notes')}")
        
        # 处理下一步计划
        if 'next_steps' in valid_columns and getattr(update, 'next_steps'):
            steps_label = column_comments.get('next_steps', '下一步行动计划')
            content.append(f"\n**{steps_label}**:\n")
            content.append(f"{getattr(update, 'next_steps')}")
        
        # 处理关键干系人
        if 'key_stakeholders' in valid_columns and getattr(update, 'key_stakeholders'):
            stakeholders_label = column_comments.get('key_stakeholders', '相关关键干系人')
            content.append(f"\n**{stakeholders_label}**:\n")
            content.append(f"{getattr(update, 'key_stakeholders')}")
        
        # 处理障碍/挑战
        if 'blockers' in valid_columns and getattr(update, 'blockers'):
            blockers_label = column_comments.get('blockers', '当前障碍或挑战')
            content.append(f"\n**{blockers_label}**:\n")
            content.append(f"{getattr(update, 'blockers')}")
        
        # 已处理的字段集合
        processed_fields = {
            # 系统字段 - 需要排除
            'id', 'opportunity_id', 'creator_id',
            
            # 已处理的业务字段
            'opportunity_name', 'update_type', 'record_date', 
            'creator', 'summary', 'detailed_notes', 'next_steps',
            'key_stakeholders', 'customer_sentiment', 'deal_probability_change', 
            'blockers'
        }
        
        # 处理剩余字段（如果有模型更新增加了新字段）
        for field_name in valid_columns:
            # 跳过已处理字段
            if field_name in processed_fields:
                continue
                
            value = getattr(update, field_name)
            if value is None:
                value = "暂无"
            
            # 格式化日期和时间
            if isinstance(value, (datetime, date)):
                value = value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else value.strftime("%Y-%m-%d")
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            content.append(f"\n**{display_name}**: {value}")
    
    return content

def format_order_info(order) -> List[str]:
    """动态处理订单信息，仅使用模型中定义的字段"""
    if not order:
        return []
        
    content = []
    order_number = getattr(order, "sales_order_number", None) or getattr(order, "unique_id", None) or "未命名订单"
    content.append(f"\n## 订单：{order_number}\n")
    
    # 获取订单模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(order))
    
    field_groups = {
        "basic": {
            "title": "",  # 基本信息不需要标题
            "fields": ["customer_name", "opportunity_name", "opportunity_number", "project_name", "contract_name", "quote_id", "sales_type", "product_type", 
                      "sales_order_number", "signing_date", "life_status", "order_type", "owner", "owner_department"]
        },
        "financial": {
            "title": "### 财务信息",
            "fields": ["sales_order_amount", "sales_order_amount_excluding_tax", "product_subscription_amount", 
                      "product_perpetual_license_amount", "maintenance_service_amount", "man_day_service_amount",
                      "arr", "arr_excluding_tax", "acv", "renew_arr", "new_arr", "split_ratio"]
        },
        "payment": {
            "title": "### 回款信息",
            "fields": ["total_payment_amount", "planned_payment_amount", "pending_payment_amount", 
                      "payment_status", "invoice_status", "invoice_completion_status", "settle_type"]
        },
        "service": {
            "title": "### 服务信息",
            "fields": ["service_start_date_subscription_maintenance", "service_end_date_subscription_maintenance", 
                      "service_duration_months", "maintenance_ratio", "expected_renewal_time_fy"]
        },
        "contract": {
            "title": "### 合同信息",
            "fields": ["contract_type", "contracting_party", "contracting_partner", "contract_attribute", 
                      "contract_archiving_status", "is_framework_order", "framework_agreement_id"]
        },
        "partner": {
            "title": "### 合作伙伴信息",
            "fields": ["partner_id", "is_general_agent", "first_level_distributor", "second_level_distributor", 
                      "third_level_distributor", "reported_partner_name", "has_sub_agents",
                      "total_margin_percentage", "commission_info"]
        },
        "delivery": {
            "title": "### 交付信息",
            "fields": ["shipping_status", "shipping_address", "delivery_time", "delivery_acceptance_progress"]
        },
        "renewal": {
            "title": "### 续约信息",
            "fields": ["renewal_status", "renewal_type"]
        },
        "cost": {
            "title": "### 成本信息",
            "fields": ["outsourcing_cost", "profit_statement"]
        },
        "system": {
            "title": "### 系统信息",
            "fields": ["creation_time", "created_by", "last_modified_time", "last_modified_by", "source", "resource"]
        }
    }
        
    # 详细信息字段（长文本）单独处理
    detail_fields = {
        "delivery_acceptance_progress": "交付/验收进展",
        "delivery_comment": "发货备注",
        "remark": "备注"
    }

    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "customer_id", "opportunity_id", "opportunity_number"}
     
    # 需要特殊处理的布尔字段 - 显示为"是/否"
    boolean_fields = {"is_general_agent", "is_framework_order", "has_sub_agents"}
    
    # 需要特殊处理的金额字段 - 格式化显示
    currency_fields = {"sales_order_amount", "product_subscription_amount", "product_perpetual_license_amount", 
                      "maintenance_service_amount", "man_day_service_amount", "outsourcing_cost", 
                      "total_payment_amount", "planned_payment_amount"}
    
    # 需要特殊处理的JSON字段
    json_fields = {"owner", "created_by", "last_modified_by", "owning_department", "renewal_type", 
                  "profit_statement", "performance_accounting_sales_department"}
  
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
                
        # 处理JSON字段
        if field_name in json_fields and value is not None:
            try:
                # 对于利润表字段，处理为Markdown表格
                if field_name == "profit_statement" and value:
                    new_value = '\n\n'
                    new_value += "| 序号 | 文件名 | 扩展名 | 创建时间 | 文件大小 | 文件路径 |\n"
                    new_value += "|------|--------|--------|-----------|----------|-----------|\n"
                    for idx, item in enumerate(value, 1):
                        time_str = datetime.fromtimestamp(item['create_time'] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        size_str = f"{item['size']:,}B"
                        new_value += f"| {idx} | {item['filename']} | {item['ext']} | {time_str} | {size_str} | {item['path']} |\n"
                    return new_value
                
                # 对于人名部门等格式为['AAA']的字段，进行元素拼接
                if isinstance(value, list):
                    return ','.join(value)
                elif isinstance(value, str):
                    return value
                else:
                    return str(value)
            except Exception:
                pass
                
        # 默认返回原值
        return value
    
    # 辅助函数：处理一组字段
    def process_field_group(group_info, condition_field=None):
        # 检查条件字段
        if condition_field and (condition_field not in valid_columns or 
                               not getattr(order, condition_field, None)):
            return []
            
        group_content = []
        has_fields = False
        
        for field_name in group_info["fields"]:
            if field_name not in valid_columns or field_name in exclude_fields:
                continue
                
            value = getattr(order, field_name, None)
            if value is None:
                value = "暂无"
            
            if not has_fields and group_info["title"]:
                group_content.append(group_info["title"])
                has_fields = True
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            formatted_value = format_field_value(field_name, value)
            group_content.append(f"- {display_name}: {formatted_value}\n")
            
        return group_content
       
    # 处理基本信息
    content.extend(process_field_group(field_groups["basic"]))
    
    # 处理财务信息
    financial_content = process_field_group(field_groups["financial"])
    if financial_content:
        content.extend(financial_content)
    
    # 处理回款信息
    payment_content = process_field_group(field_groups["payment"])
    if payment_content:
        content.extend(payment_content)
    
    # 处理服务信息
    service_content = process_field_group(field_groups["service"])
    if service_content:
        content.extend(service_content)
    
    # 处理合同信息
    contract_content = process_field_group(field_groups["contract"])
    if contract_content:
        content.extend(contract_content)
    
    # 处理合作伙伴信息
    partner_content = process_field_group(field_groups["partner"])
    if partner_content:
        content.extend(partner_content)
    
    
    # 处理交付信息
    delivery_content = process_field_group(field_groups["delivery"])
    if delivery_content:
        content.extend(delivery_content)
    
    # 处理续约信息
    renewal_content = process_field_group(field_groups["renewal"])
    if renewal_content:
        content.extend(renewal_content)
    
    # 处理成本信息
    cost_content = process_field_group(field_groups["cost"])
    if cost_content:
        content.extend(cost_content)
    
    # 处理详细信息字段（长文本）
    for field_name, display_title in detail_fields.items():
        if field_name not in valid_columns:
            continue
            
        value = getattr(order, field_name, None)
        if value is None:
            value = "暂无"
        
        title = column_comments.get(field_name, display_title)
        content.append(f"\n### {title}")
        content.append(value)
    
    content.append(f"\n")
    # 处理系统信息
    system_content = process_field_group(field_groups["system"])
    if system_content:
        content.extend(system_content)
    
    return content

def format_payment_plan_info(payment_plan) -> List[str]:
    """动态处理回款计划信息，仅使用模型中定义的字段"""
    if not payment_plan:
        return []
    
    # 如果回款计划被删除，则不显示
    is_deleted = getattr(payment_plan, "is_deleted", None)
    if is_deleted:
        return []
    
    content = []
    payment_plan_name = getattr(payment_plan, 'name', 'None') or getattr(payment_plan, "unique_id", None) or '未知回款计划'
    content.append(f"\n### 回款计划：{payment_plan_name}")

    # 获取回款计划模型的列注释和字段名
    column_comments, valid_columns = get_column_comments_and_names(type(payment_plan))
    
    # 定义字段组
    field_groups = {
        "basic": {
            "title": "基本信息",
            "fields": ["name", "order_id", "account_name", "plan_payment_status", 
                      "plan_payment_method", "plan_payment_ratio", "plan_payment_time", 
                      "latest_plan_payment_date", "target_payment_date", "actual_payment_date"]
        },
        "amount": {
            "title": "金额信息",
            "fields": ["order_amount", "plan_payment_amount", "actual_payment_amount", 
                      "pending_payment_amount"]
        },
        "contract": {
            "title": "合同信息",
            "fields": ["contract_party", "contract_entity", "contract_date"]
        },
        "overdue": {
            "title": "逾期信息",
            "fields": ["first_payment_overdue_days", "latest_payment_overdue_days", 
                      "first_plan_overdue_month", "overdue_reason"]
        },
        "plan": {
            "title": "计划信息",
            "fields": ["next_plan", "next_plan_description"]
        },
        "fiscal": {
            "title": "财务周期信息",
            "fields": ["booking_fiscal_year", "first_plan_payment_fiscal_quarter", 
                      "latest_plan_payment_fiscal_quarter", "actual_payment_fiscal_quarter"]
        },
        "status": {
            "title": "状态信息",
            "fields": ["remind_time", "life_status", "lock_status", "lock_user", "lock_rule", "backend_process_status", "life_status_before_invalid"]
        },
        "owner": {
            "title": "责任方信息",
            "fields": ["owner", "owner_department", "out_owner", "relevant_team", 
                      "data_own_department", "approve_employee_id"]
        },
        "system": {
            "title": "系统信息",
            "fields": ["create_time", "created_by", "last_modified_time", "last_modified_by", 
                      "origin_source", "out_resources"]
        }
    }
    
    # 详细信息字段（长文本）单独处理
    detail_fields = {
        "remark": "备注",
        "overdue_description_and_next_plan": "逾期说明及下一步计划",
        "overdue_payment_reason": "逾期回款原因",
        "next_plan_description": "下一步计划说明"
    }
    
    # 需要排除的字段
    exclude_fields = {"id", "unique_id", "account_id", "approve_employee_id", "out_tenant_id", "order_id", "partner_id", "extend_obj_data_id",
                      "is_deleted", "version", "data_refresh_flag", "relevant_team",
                      "order_by", "backend_process_status", "record_type", "action_tag"}
    
    # 需要特殊处理的金额字段 - 格式化显示
    currency_fields = {"plan_payment_amount", "actual_payment_amount", "order_amount", "pending_payment_amount"}
    
    # 需要特殊处理的JSON字段
    json_fields = {"owner", "out_owner", "created_by", "last_modified_by", "lock_user", "approve_employee_id", "owner_department", "data_own_department", "attachment", "overdue_reason"}
    
    # 辅助函数：处理字段值的格式化
    def format_field_value(field_name, value):
        # 处理 None 值
        if value is None:
            return "暂无"
        
        if field_name == "contract_date":
            ts = int(value) / 1000
            value = date.fromtimestamp(ts)
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
        # 处理JSON字段
        if field_name in json_fields and value is not None:
            try:
                # 对于附件字段，处理为Markdown表格
                if field_name == "attachment" and value:
                    new_value = '\n\n'
                    new_value += "| 序号 | 文件名 | 扩展名 | 创建时间 | 文件大小 | 文件路径 |\n"
                    new_value += "|------|--------|--------|-----------|----------|-----------|\n"
                    for idx, item in enumerate(value, 1):
                        time_str = datetime.fromtimestamp(item['create_time'] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        size_str = f"{item['size']:,}B"
                        new_value += f"| {idx} | {item['filename']} | {item['ext']} | {time_str} | {size_str} | {item['path']} |\n"
                    return new_value
                
                # 对于人名部门等格式为['AAA']的字段，进行元素拼接
                if isinstance(value, list):
                    return ','.join(value)
                elif isinstance(value, str):
                    return value
                else:
                    return str(value)
            except Exception:
                pass      
        # 默认返回原值
        return value
    
    # 辅助函数：处理一组字段
    def process_field_group(group_info):
        group_content = []
        has_fields = False
        
        for field_name in group_info["fields"]:
            if field_name not in valid_columns or field_name in exclude_fields:
                continue
                
            value = getattr(payment_plan, field_name, None)
            if value is None:
                value = "暂无"
                
            if not has_fields and group_info["title"]:
                group_content.append(f"**{group_info['title']}**:\n")
                has_fields = True
                
            display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
            formatted_value = format_field_value(field_name, value)
            group_content.append(f"- {display_name}: {formatted_value}\n")
            
        return group_content
    
    # 处理基本信息
    content.extend(process_field_group(field_groups["basic"]))
    
    # 处理金额信息
    amount_content = process_field_group(field_groups["amount"])
    if amount_content:
        content.extend(amount_content)

    # 处理合同信息
    contract_content = process_field_group(field_groups["contract"])
    if contract_content:
        content.extend(contract_content)

    # 处理逾期信息
    overdue_content = process_field_group(field_groups["overdue"])
    if overdue_content:
        content.extend(overdue_content)

    # 处理计划信息
    plan_content = process_field_group(field_groups["plan"])
    if plan_content:
        content.extend(plan_content)

    # 处理财务周期信息
    fiscal_content = process_field_group(field_groups["fiscal"])
    if fiscal_content:
        content.extend(fiscal_content)
        content.append("> 说明：季度格式为'FY25Q1'，表示财年2025第一季度。财年从自然年4月1日开始计算，例如2024年4月1日开始的季度为FY25Q1。\n")

    # 处理状态信息
    status_content = process_field_group(field_groups["status"])
    if status_content:
        content.extend(status_content)

    # 处理责任方信息
    owner_content = process_field_group(field_groups["owner"])
    if owner_content:
        content.extend(owner_content)
    
    # 处理详细信息字段（长文本）
    for field_name, display_title in detail_fields.items():
        if field_name not in valid_columns:
            continue
            
        value = getattr(payment_plan, field_name, None)
        if value is None:
            value = "暂无"
        
        title = column_comments.get(field_name, display_title)
        content.append(f"\n**{title}**:\n")
        content.append(f"{value}\n")
    
    # 处理系统信息
    system_content = process_field_group(field_groups["system"])
    if system_content:
        content.extend(system_content)
    
    # 处理所有其他未归类字段
    all_processed_fields = set()
    for group in field_groups.values():
        all_processed_fields.update(group["fields"])
    all_processed_fields.update(exclude_fields)
    all_processed_fields.update(detail_fields.keys())
    
    other_fields = []
    for field_name in valid_columns:
        if field_name in all_processed_fields:
            continue
            
        value = getattr(payment_plan, field_name, None)
        if value is None:
            value = "暂无"
            
        display_name = column_comments.get(field_name, field_name.replace('_', ' ').title())
        formatted_value = format_field_value(field_name, value)
        other_fields.append(f"- {display_name}: {formatted_value}\n")
    
    if other_fields:
        content.append("**其他信息**:\n")
        content.extend(other_fields)
    
    return content
