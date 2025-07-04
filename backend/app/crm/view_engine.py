import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Type
from uuid import UUID

from fastapi import Query
from fastapi_pagination import Params
from fastapi_pagination.ext.sqlmodel import paginate
from sqlalchemy import func, or_, and_
from sqlmodel import Session, select
from sqlalchemy.orm import joinedload, load_only, contains_eager

from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_accounts import CRMAccount
from app.rag.chat.crm_authority import get_user_crm_authority, CrmDataType
from app.api.routes.crm.models import (
    ViewType,
    FilterOperator,
    FilterCondition,
    GroupCondition,
    CrmViewRequest,
    FieldMetadata,
)

logger = logging.getLogger(__name__)

class ViewRegistry:
    def __init__(self):
        self.views = {}
        self.field_metadata = {}
        # 定义字段来源
        self.field_sources = {
            "account_fields": {
                "customer_name": "客户名称",
                "industry": "客户行业",
                "customer_level": "客户级别",
                "person_in_charge": "客户负责人",
                "unique_id": "客户唯一标识"
            },
            "opportunity_fields": {
                "opportunity_name": "商机名称",
                "opportunity_type": "商机类型",
                "owner": "销售",
                "estimated_acv": "预估ACV",
                "opportunity_stage": "销售阶段",
                "forecast_type": "预测类型",
                "expected_closing_quarter": "预计成交季度",
                "expected_closing_date": "预计成交日期",
                "sl_pull_in": "SL Pull IN",
                "owner_main_department": "部门",
                "ppl_product_type": "PPL产品类型",
                "opportunity_source": "商机来源",
                "sales_order_archive_status": "销售订单归档状态",
                "general_agent": "总代理",
                "presales_owner": "售前负责人",
                "unique_id": "商机唯一标识"
            }
        }
        # 固定字段列表
        self.fixed_fields = {
            "owner_main_department",  # 部门
            "owner",                  # 销售
            "customer_level",         # 客户级别
            "expected_closing_quarter", # 预计成交季度
            "opportunity_stage",      # 销售阶段
            "forecast_type",         # 预测类型
            "customer_name"          # 客户名称
        }
        # 固定枚举类型字段
        self.static_enum_fields = {
            # "opportunity_stage": OpportunityStage,
            # "forecast_type": ForecastType,
            # "opportunity_type": OpportunityType,
            # "customer_level": CustomerLevel,
            # "sl_pull_in": SL_PULL_IN
        }
        # 搜索类型字段
        self.searchable_fields = [
            "customer_name",
            "expected_closing_quarter",
            "expected_closing_date",
        ]
        # 动态枚举类型字段
        self.dynamic_enum_fields = [
            "owner_main_department",
            "owner",
            "ppl_product_type",
            "opportunity_source",
            "sales_order_archive_status",
            "general_agent",
            "presales_owner",
            # 原为固定枚举类型字段，现在改为动态枚举类型字段，适配不同公司不同枚举类型
            "opportunity_stage",
            "forecast_type",
            "opportunity_type",
            "customer_level",
            "sl_pull_in"
        ]
        
        self.register_standard_view_fields()
        self.register_filter_option_views()

    def register_standard_view_fields(self):
        """注册视图字段"""
        # 注册客户表字段
        for field, display_name in self.field_sources["account_fields"].items():
            self.register_field_metadata(FieldMetadata(
                name=field,
                display_name=display_name,
                type="string",
                description=display_name,
                source="account",  # 标记字段来源
                fixed=field in self.fixed_fields  # 只有指定的字段是固定的
            ))
        
        # 注册商机表字段
        for field, display_name in self.field_sources["opportunity_fields"].items():
            # 特殊处理 estimated_acv 字段
            field_type = "integer" if field == "estimated_acv" else "string"
            self.register_field_metadata(FieldMetadata(
                name=field,
                display_name=display_name,
                type=field_type,
                description=display_name,
                source="opportunity",  # 标记字段来源
                fixed=field in self.fixed_fields  # 只有指定的字段是固定的
            ))
        
        # 注册标准视图
        self.register_view(ViewType.STANDARD, [
            # account info
            "customer_name",
            "industry",
            "customer_level",
            "person_in_charge",
            
            # opportunity info
            "opportunity_name",
            "opportunity_type",
            "owner",
            "estimated_acv",
            "opportunity_stage",
            "forecast_type",
            "expected_closing_date",
            "expected_closing_quarter",
            "sl_pull_in",
            "owner_main_department",
            "ppl_product_type",
            "opportunity_source",
            "sales_order_archive_status",
            "general_agent",
            "presales_owner"
        ], "商机视图")

    def register_view(self, view_type: ViewType, fields: List[str], description: str = None):
        """注册视图"""
        self.views[view_type] = {
            "fields": fields,
            "description": description
        }

    def register_field_metadata(self, field_metadata: FieldMetadata):
        """注册字段元数据"""
        self.field_metadata[field_metadata.name] = field_metadata

    def get_view_fields(self, view_type: ViewType) -> List[str]:
        """获取视图字段"""
        if view_type not in self.views:
            raise ValueError(f"View {view_type} not defined")
        return self.views[view_type]["fields"]

    def get_field_metadata(self, field_name: str) -> FieldMetadata:
        """获取字段元数据"""
        if field_name not in self.field_metadata:
            raise ValueError(f"Field metadata not found for {field_name}")
        return self.field_metadata[field_name]

    def get_all_views(self) -> Dict[str, Any]:
        """获取所有视图"""
        return self.views

    def get_all_fields(self) -> Dict[str, FieldMetadata]:
        """获取所有字段元数据"""
        return self.field_metadata

    def register_filter_option_views(self):
        """注册筛选条件选项视图"""
        # 注册筛选视图
        self.register_view(
            view_type=ViewType.FILTER_OPTIONS,
            fields=[
                # 固定枚举字段
                "opportunity_stage",
                "forecast_type",
                "opportunity_type",
                "customer_level",
                "sl_pull_in",
                
                # 动态枚举字段
                "owner_main_department",
                "owner",
                "ppl_product_type",
                "opportunity_source",
                "sales_order_archive_status",
                "general_agent",
                "presales_owner",
                
                # 搜索字段
                "customer_name",
                "expected_closing_quarter",
                "expected_closing_date"
            ],
            description="筛选条件选项"
        )

    def get_filter_option_fields(self) -> List[str]:
        """获取筛选条件字段列表"""
        return self.dynamic_enum_fields

    def is_static_enum_field(self, field: str) -> bool:
        """判断是否为固定枚举类型字段"""
        return field in self.static_enum_fields

    def is_searchable_field(self, field: str) -> bool:
        """判断是否为可搜索字段"""
        return field in self.searchable_fields

    def is_dynamic_enum_field(self, field: str) -> bool:
        """判断是否为动态枚举类型字段"""
        return field in self.dynamic_enum_fields

    def get_static_enum_values(self, field: str) -> List[str]:
        """获取固定枚举字段的所有可能值"""
        if field in self.static_enum_fields:
            return [e.value for e in self.static_enum_fields[field]]
        return []

    def get_all_enum_fields(self) -> List[str]:
        """获取所有枚举类型字段（包括固定和动态）"""
        return list(self.static_enum_fields.keys()) + self.dynamic_enum_fields

    def get_field_source(self, field_name: str) -> str:
        """获取字段来源"""
        if field_name in self.field_sources["account_fields"]:
            return "account"
        elif field_name in self.field_sources["opportunity_fields"]:
            return "opportunity"
        return None

class CrmViewEngine:
    def __init__(self, view_registry: ViewRegistry):
        self.view_registry = view_registry
        self.model = CRMOpportunity
        self.account_model = CRMAccount
        
        # 定义需要特殊处理的 JSON 数组字段
        self.json_array_fields = ["owner", "presales_owner", "person_in_charge"]
        
        # 定义客户表固定字段
        self.account_fields = [
            self.account_model.unique_id,
            self.account_model.customer_name,
            self.account_model.customer_level,
            self.account_model.industry,
            self.account_model.person_in_charge
        ]
        
        self.account_field_names = list(self.view_registry.field_sources["account_fields"].keys())

    def get_filter_options(self, db_session: Session, user_id: Optional[UUID] = None) -> Dict[str, Any]:
        """获取筛选条件选项"""        
        if not self.view_registry.get_all_enum_fields():
            return {}
        
        # 构建基础查询
        query = db_session.query(self.model)
        
        # 应用权限过滤
        authority = None
        role = None
        if user_id:
            try:
                authority, role = get_user_crm_authority(user_id)
                
                # 如果是管理员，跳过权限过滤
                if role != "admin":
                    # 如果没有权限，直接返回空结果
                    if authority.is_empty():
                        logger.info(f"No authorized items found for user {user_id}")
                        return {}
                    
                    crm_type = CrmDataType("crm_opportunity")
                    if hasattr(self.model, "unique_id"):
                        query = query.filter(self.model.unique_id.in_(authority.authorized_items[crm_type]))
            except ValueError:
                logger.warning(f"Invalid entity type")
                return {}
        
        # 获取各字段的唯一值
        options = {
            "enum_fields": {},      # 枚举类型字段（包括固定和动态）
            "searchable_fields": [] # 可搜索字段
        }
        
        # 构建部门到负责人的映射
        owner_dept_query = query.distinct(self.model.owner, self.model.owner_main_department)
        owner_dept_values = owner_dept_query.all()
        
        dept_to_owners = {}
        owner_to_dept = {}
        
        for row in owner_dept_values:
            owner = row.owner
            dept = row.owner_main_department
            
            if owner is not None and dept is not None:
                owner = self._clean_json_array_value(owner)
                
                if dept not in dept_to_owners:
                    dept_to_owners[dept] = set()
                dept_to_owners[dept].add(owner)
                owner_to_dept[owner] = dept
        
        # 构建级联选择器格式的数据
        options["owner_dept_mapping"] = [
            {
                "label": dept,
                "options": [
                    {
                        "label": owner,
                        "value": {
                            "owner_main_department": dept,
                            "owner": owner
                        }
                    } for owner in sorted(owners)
                ]
            } for dept, owners in sorted(dept_to_owners.items())
        ]
        
        # 处理所有字段
        all_fields = self.view_registry.get_all_enum_fields() + self.view_registry.searchable_fields
        
        for field in all_fields:
            if field not in ["owner", "owner_main_department"]:
                # 获取字段元数据
                field_metadata = self.view_registry.get_field_metadata(field)
                
                # 处理固定枚举类型字段
                if self.view_registry.is_static_enum_field(field):
                    enum_values = self.view_registry.get_static_enum_values(field)
                    options["enum_fields"][field] = {
                        "values": enum_values,
                        "display_name": field_metadata.display_name,
                        "fixed": field_metadata.fixed
                    }
                # 处理可搜索字段
                elif self.view_registry.is_searchable_field(field):
                    options["searchable_fields"].append({
                        "name": field,
                        "display_name": field_metadata.display_name,
                        "fixed": field_metadata.fixed
                    })
                # 处理动态枚举类型字段
                elif self.view_registry.is_dynamic_enum_field(field):
                    field_source = self.view_registry.get_field_source(field)
                    field_values = []
                    if field_source == "account" and hasattr(self.account_model, field):
                        column = getattr(self.account_model, field)
                        values = db_session.query(column).distinct().all()
                        for v in values:
                            if v[0] is not None:
                                field_values.append(v[0])
                    elif hasattr(self.model, field):
                        column = getattr(self.model, field)
                        values = query.distinct(column).values(column)
                        for v in values:
                            if v[0] is not None:
                                if field == "expected_closing_date" and isinstance(v[0], str):
                                    date_value = v[0].split('T')[0]
                                    field_values.append(date_value)
                                elif field in self.json_array_fields and isinstance(v[0], str):
                                    field_values.append(self._clean_json_array_value(v[0]))
                                else:
                                    field_values.append(v[0])
                    if field_values:
                        options["enum_fields"][field] = {
                            "values": sorted(field_values),
                            "display_name": field_metadata.display_name,
                            "fixed": field_metadata.fixed
                        }
        
        return options

    def build_base_query(self, db_session: Session, request: CrmViewRequest, user_id: Optional[UUID] = None):
        """构建基础查询"""
        fields_to_query = self._get_fields_for_view(request.view_type, request.custom_fields)
        
        account_fields_to_query = []
        opportunity_fields_to_query = []
        
        for field in fields_to_query:
            field_source = self.view_registry.get_field_source(field)
            if field_source == "account" and hasattr(self.account_model, field):
                account_fields_to_query.append(getattr(self.account_model, field))
            elif field_source == "opportunity" and hasattr(self.model, field):
                opportunity_fields_to_query.append(getattr(self.model, field))
        
        query = select(self.model)
        
        if opportunity_fields_to_query:
            query = query.options(load_only(*opportunity_fields_to_query))
        
        query = query.outerjoin(self.model.account)
        
        if account_fields_to_query:
            query = query.options(
                contains_eager(self.model.account).load_only(*account_fields_to_query)
            )
        
        if user_id:
            crm_authority, role = get_user_crm_authority(user_id)
            
            if role != "admin":
                if crm_authority.is_empty():
                    logger.info(f"No authorized items found for user {user_id}")
                    return select(self.model).where(False)
                
                authorized_opportunity_ids = crm_authority.authorized_items.get(CrmDataType.OPPORTUNITY, set())
                authorized_account_ids = crm_authority.authorized_items.get(CrmDataType.ACCOUNT, set())
                
                authority_conditions = []
                
                if authorized_opportunity_ids:
                    authority_conditions.append(self.model.unique_id.in_(authorized_opportunity_ids))
                
                if authorized_account_ids:
                    authority_conditions.append(self.account_model.unique_id.in_(authorized_account_ids))
                
                if authority_conditions:
                    query = query.where(and_(*authority_conditions))
        
        if request.filters:
            for filter_condition in request.filters:
                logger.info(f"filter_condition: {filter_condition}")
                if filter_condition.field in self.account_field_names:
                    query = self._apply_filter(query, self.account_model, filter_condition)
                    logger.info(f"after account filter: {query}")
                else:
                    query = self._apply_filter(query, self.model, filter_condition)
                    logger.info(f"after opportunity filter: {query}")
        
        # 处理 advanced_filters，独立于 filters
        if request.advanced_filters:
            logger.info(f"advanced_filters: {request.advanced_filters}")
            # advanced_filters 自己判断应该应用在哪个模型上
            # 通过分析 advanced_filters 中的字段来决定
            query = self._apply_advanced_filters_smart(query, request.advanced_filters)
            logger.info(f"after advanced filter: {query}")
        
        if request.group_by:
            query, is_grouped = self._apply_grouping(query, self.model, request.group_by)
        else:
            is_grouped = False
        
        if request.sort_by:
            query = self._apply_sorting(query, self.model, request.sort_by, request.sort_direction)
        
        logger.info(f"final query: {query}")
        return query

    def execute_view_query(self, db_session: Session, request: CrmViewRequest, user_id: Optional[UUID] = None) -> Dict[str, Any]:
        """执行视图查询"""
        # 构建基础查询
        query = self.build_base_query(db_session, request, user_id)
        
        # 创建分页参数
        params = Params(page=request.page, size=request.page_size)
        
        # 执行分页查询
        page_result = paginate(db_session, query, params)
        
        # 转换结果
        data = self._transform_results(
            page_result.items,
            self._get_fields_for_view(request.view_type, request.custom_fields),
            False
        )
        
        # 返回结果
        return {
            "data": data,
            "view_type": request.view_type,
            "total": page_result.total,
            "page": page_result.page,
            "page_size": page_result.size,
            "total_pages": page_result.pages
        }

    def _get_fields_for_view(self, view_type: ViewType, custom_fields: Optional[List[str]]) -> List[str]:
        """获取视图字段"""
        if view_type == ViewType.CUSTOM and custom_fields:
            return custom_fields
        else:
            return self.view_registry.get_view_fields(view_type)

    def _apply_filter(self, query, model, filter_condition: FilterCondition):
        """应用过滤条件"""
        field = filter_condition.field
        op = filter_condition.operator
        value = filter_condition.value
        
        if field in self.account_field_names:
            logger.info(f"account field: {field}, type: {type(model)}")
            if model == self.account_model:
                if hasattr(model, field):
                    column = getattr(model, field)
                else:
                    return query
            else:
                if hasattr(self.model, "account") and hasattr(self.model.account, field):
                    column = getattr(self.model.account, field)
                else:
                    return query
        else:
            if hasattr(self.model, field):
                column = getattr(self.model, field)
            else:
                return query
        
        # 使用 _build_filter_condition 来构建条件，确保一致性
        filter_condition_result = self._build_filter_condition(column, filter_condition)
        if filter_condition_result is not None:
            return query.where(filter_condition_result)
        
        return query

    def _apply_advanced_filters(self, query, model, advanced_filters):
        """递归应用高级过滤条件"""
        if not advanced_filters:
            return query
            
        logger.info(f"Applying advanced filters on model {model.__name__}: {advanced_filters}")
            
        # 处理NOT条件
        if "NOT" in advanced_filters:
            condition = advanced_filters["NOT"]
            if isinstance(condition, dict):
                if "AND" in condition or "OR" in condition:
                    # 递归处理嵌套的AND/OR条件
                    sub_query = self._apply_advanced_filters(query, model, condition)
                    return query.where(~sub_query.whereclause)
                else:
                    # 处理基本过滤条件
                    filter_condition = FilterCondition(**condition)
                    column = self._get_column_for_field(model, filter_condition.field)
                    if column:
                        filter_condition_result = self._build_filter_condition(column, filter_condition)
                        if filter_condition_result is not None:
                            return query.where(~filter_condition_result)
            
        # 处理AND条件
        if "AND" in advanced_filters:
            conditions = []
            for condition in advanced_filters["AND"]:
                if isinstance(condition, dict):
                    if "AND" in condition or "OR" in condition or "NOT" in condition:
                        # 递归处理嵌套的AND/OR/NOT条件
                        sub_query = self._apply_advanced_filters(query, model, condition)
                        if sub_query.whereclause is not None:
                            conditions.append(sub_query.whereclause)
                    else:
                        # 处理基本过滤条件
                        filter_condition = FilterCondition(**condition)
                        column = self._get_column_for_field(model, filter_condition.field)
                        logger.info(f"Processing filter: {filter_condition.field}, column: {column}, model: {model.__name__}")
                        if column:
                            filter_condition_result = self._build_filter_condition(column, filter_condition)
                            if filter_condition_result is not None:
                                conditions.append(filter_condition_result)
                                logger.info(f"Added condition for {filter_condition.field}")
                            else:
                                logger.warning(f"Filter condition result is None for {filter_condition.field}")
                        else:
                            logger.warning(f"Column not found for field {filter_condition.field} in model {model.__name__}")
            
            if conditions:
                logger.info(f"Applying {len(conditions)} AND conditions")
                return query.where(and_(*conditions))
                
        # 处理OR条件
        elif "OR" in advanced_filters:
            conditions = []
            for condition in advanced_filters["OR"]:
                if isinstance(condition, dict):
                    if "AND" in condition or "OR" in condition or "NOT" in condition:
                        # 递归处理嵌套的AND/OR/NOT条件
                        sub_query = self._apply_advanced_filters(query, model, condition)
                        if sub_query.whereclause is not None:
                            conditions.append(sub_query.whereclause)
                    else:
                        # 处理基本过滤条件
                        filter_condition = FilterCondition(**condition)
                        column = self._get_column_for_field(model, filter_condition.field)
                        logger.info(f"Processing filter: {filter_condition.field}, column: {column}, model: {model.__name__}")
                        if column:
                            filter_condition_result = self._build_filter_condition(column, filter_condition)
                            if filter_condition_result is not None:
                                conditions.append(filter_condition_result)
                                logger.info(f"Added condition for {filter_condition.field}")
                            else:
                                logger.warning(f"Filter condition result is None for {filter_condition.field}")
                        else:
                            logger.warning(f"Column not found for field {filter_condition.field} in model {model.__name__}")
            
            if conditions:
                logger.info(f"Applying {len(conditions)} OR conditions")
                return query.where(or_(*conditions))
        
        return query

    def _get_column_for_field(self, model, field_name):
        """获取字段对应的列，支持 account 字段"""
        if field_name in self.account_field_names:
            logger.info(f"account field: {field_name}, model type: {type(model)}")
            if model == self.account_model:
                # 如果当前模型是 account 模型，直接获取字段
                if hasattr(model, field_name):
                    return getattr(model, field_name)
            else:
                # 如果当前模型是 opportunity 模型，通过关联获取 account 字段
                # 使用 CRMAccount 模型直接获取字段
                if hasattr(self.account_model, field_name):
                    return getattr(self.account_model, field_name)
        else:
            # 普通字段，直接从当前模型获取
            if hasattr(model, field_name):
                return getattr(model, field_name)
        
        return None

    def _build_filter_condition(self, column, filter_condition: FilterCondition):
        """构建过滤条件"""
        op = filter_condition.operator
        value = filter_condition.value
        
        # 处理空值情况
        if value is None and op not in [FilterOperator.IS_NULL, FilterOperator.NOT_NULL]:
            return None
            
        # 获取列的类型信息
        column_type = column.type
        
        # 特殊处理 JSON 数组字段
        if column.name in self.json_array_fields:
            return self._build_json_array_filter_condition(column, op, value)
        
        try:
            # 根据操作符构建条件
            if op == FilterOperator.EQ:
                return column == self._convert_value(value, column_type)
            elif op == FilterOperator.NEQ:
                return column != self._convert_value(value, column_type)
            elif op == FilterOperator.GT:
                return column > self._convert_value(value, column_type)
            elif op == FilterOperator.GTE:
                return column >= self._convert_value(value, column_type)
            elif op == FilterOperator.LT:
                return column < self._convert_value(value, column_type)
            elif op == FilterOperator.LTE:
                return column <= self._convert_value(value, column_type)
            elif op == FilterOperator.IN:
                if not isinstance(value, list):
                    value = [value]
                return column.in_([self._convert_value(v, column_type) for v in value])
            elif op == FilterOperator.NOT_IN:
                if not isinstance(value, list):
                    value = [value]
                return ~column.in_([self._convert_value(v, column_type) for v in value])
            elif op == FilterOperator.LIKE:
                return column.like(f"%{value}%")
            elif op == FilterOperator.ILIKE:
                return column.ilike(f"%{value}%")
            elif op == FilterOperator.IS_NULL:
                return column.is_(None)
            elif op == FilterOperator.NOT_NULL:
                return column.isnot(None)
            elif op == FilterOperator.BETWEEN:
                if isinstance(value, list) and len(value) == 2:
                    return column.between(
                        self._convert_value(value[0], column_type),
                        self._convert_value(value[1], column_type)
                    )
            elif op == FilterOperator.NOT:
                return ~self._build_filter_condition(column, FilterCondition(
                    field=filter_condition.field,
                    operator=value.get("operator"),
                    value=value.get("value")
                ))
        except (ValueError, TypeError) as e:
            # 处理类型转换错误
            raise ValueError(f"Invalid value type for field {filter_condition.field}: {str(e)}")
            
        return None

    def _build_json_array_filter_condition(self, column, op, value):
        """构建 JSON 数组字段的过滤条件"""
        if op == FilterOperator.EQ:
            # 对于 JSON 数组字段，使用字符串匹配，因为 owner 字段可能存储为 ["孙琦"] 格式
            json_value = f'["{value}"]'
            return column == json_value
        elif op == FilterOperator.NEQ:
            json_value = f'["{value}"]'
            return column != json_value
        elif op == FilterOperator.IN:
            if not isinstance(value, list):
                value = [value]
            # 对于 IN 操作，检查是否包含任何一个值
            conditions = []
            for v in value:
                json_value = f'["{v}"]'
                conditions.append(column == json_value)
            return or_(*conditions)
        elif op == FilterOperator.NOT_IN:
            if not isinstance(value, list):
                value = [value]
            # 对于 NOT_IN 操作，检查不包含任何一个值
            conditions = []
            for v in value:
                json_value = f'["{v}"]'
                conditions.append(column != json_value)
            return and_(*conditions)
        elif op == FilterOperator.LIKE:
            # 对于 LIKE 操作，使用字符串匹配
            return column.like(f"%{value}%")
        elif op == FilterOperator.ILIKE:
            return column.ilike(f"%{value}%")
        elif op == FilterOperator.IS_NULL:
            return column.is_(None)
        elif op == FilterOperator.NOT_NULL:
            return column.isnot(None)
        else:
            # 对于其他操作符，使用默认处理
            return column == value

    def _convert_value(self, value, column_type):
        """转换值的类型以匹配列类型"""
        if value is None:
            return None
            
        try:
            # 安全地处理 SQLAlchemy 类型
            if hasattr(column_type, 'python_type'):
                return column_type.python_type(value)
            return value
        except (ValueError, TypeError, NotImplementedError):
            # 如果转换失败，返回原始值
            return value

    def _validate_filter_condition(self, condition: Dict[str, Any]) -> bool:
        """验证过滤条件的有效性"""
        if not isinstance(condition, dict):
            return False
            
        # 检查基本过滤条件
        if all(key in condition for key in ["field", "operator"]):
            return True
            
        # 检查复合条件
        if any(key in condition for key in ["AND", "OR", "NOT"]):
            if "AND" in condition:
                return all(self._validate_filter_condition(c) for c in condition["AND"])
            elif "OR" in condition:
                return all(self._validate_filter_condition(c) for c in condition["OR"])
            elif "NOT" in condition:
                return self._validate_filter_condition(condition["NOT"])
                
        return False

    def _apply_grouping(self, query, model, group_by: List[GroupCondition]):
        """应用分组"""
        group_columns = []
        for group in group_by:
            if hasattr(model, group.field):
                group_columns.append(getattr(model, group.field))
        
        if group_columns:
            query = query.group_by(*group_columns)
            return query, True
        return query, False

    def _apply_sorting(self, query, model, sort_by: str, sort_direction: str):
        """应用排序"""
        if hasattr(model, sort_by):
            column = getattr(model, sort_by)
            if sort_direction.lower() == "desc":
                return query.order_by(column.desc())
            return query.order_by(column.asc())
        return query

    def _transform_results(self, results: List[Any], fields: List[str], is_grouped: bool) -> List[Dict[str, Any]]:
        """转换查询结果为客户到商机的一对多关系结构"""
        # 使用字典来存储客户信息，key 是客户 ID
        accounts_dict = {}
        
        for result in results:
            # 获取客户信息
            if isinstance(result, self.account_model):
                # 如果是从客户表查询的结果
                account = result
                account_id = account.unique_id
                
                # 如果这个客户还没有被处理过，创建客户记录
                if account_id not in accounts_dict:
                    accounts_dict[account_id] = {
                        "unique_id": account_id,
                        "customer_name": account.customer_name,
                        "industry": account.industry,
                        "customer_level": account.customer_level,
                        "person_in_charge": self._clean_json_array_value(account.person_in_charge) if account.person_in_charge else None,
                        "opportunities": []  # 初始化商机列表
                    }
            else:
                # 如果是从商机表查询的结果
                if not hasattr(result, "account"):
                    continue
                    
                account = result.account
                if not account:
                    continue
                    
                account_id = account.unique_id
                
                # 如果这个客户还没有被处理过，创建客户记录
                if account_id not in accounts_dict:
                    accounts_dict[account_id] = {
                        "unique_id": account_id,
                        "customer_name": account.customer_name,
                        "industry": account.industry,
                        "customer_level": account.customer_level,
                        "person_in_charge": self._clean_json_array_value(account.person_in_charge) if account.person_in_charge else None,
                        "opportunities": []  # 初始化商机列表
                    }
                
                # 构建商机信息
                opportunity = {
                    "unique_id": result.unique_id  # 添加商机 unique_id
                }
                for field in fields:
                    if hasattr(result, field):
                        value = getattr(result, field)
                        if field in self.json_array_fields and isinstance(value, str):
                            value = self._clean_json_array_value(value)
                        elif field == "expected_closing_date" and isinstance(value, str):
                            value = value.split('T')[0]  # 只保留日期部分
                        opportunity[field] = value
                
                # 将商机添加到对应客户的商机列表中
                accounts_dict[account_id]["opportunities"].append(opportunity)
        
        # 将字典转换为列表
        return list(accounts_dict.values())

    def _clean_json_array_value(self, value: str) -> str:
        try:
            clean_value = value.replace('\\"', '"')
            if clean_value.startswith('[') and clean_value.endswith(']'):
                return clean_value[2:-2]  # 移除 [" 和 "]
        except Exception:
            pass
        return value

    def _apply_advanced_filters_smart(self, query, advanced_filters):
        """智能应用高级过滤条件，根据字段类型自动选择模型"""
        if not advanced_filters:
            return query
        
        # 分析 advanced_filters 中的字段类型
        fields_info = self._analyze_advanced_filters_fields(advanced_filters)
        
        # 如果只包含 account 字段，应用在 account_model 上
        if fields_info['has_account_fields'] and not fields_info['has_opportunity_fields']:
            logger.info("Advanced filters contain only account fields, applying on account model")
            return self._apply_advanced_filters(query, self.account_model, advanced_filters)
        
        # 如果只包含 opportunity 字段，应用在 opportunity model 上
        elif fields_info['has_opportunity_fields'] and not fields_info['has_account_fields']:
            logger.info("Advanced filters contain only opportunity fields, applying on opportunity model")
            return self._apply_advanced_filters(query, self.model, advanced_filters)
        
        # 如果包含混合字段，需要分别处理
        elif fields_info['has_account_fields'] and fields_info['has_opportunity_fields']:
            logger.info("Advanced filters contain mixed fields, applying on opportunity model with account joins")
            # 对于混合字段，应用在 opportunity model 上，account 字段通过 _get_column_for_field 方法处理
            return self._apply_advanced_filters(query, self.model, advanced_filters)
        
        # 如果没有识别出字段类型，默认应用在 opportunity model 上
        else:
            logger.info("No recognized fields in advanced filters, applying on opportunity model")
            return self._apply_advanced_filters(query, self.model, advanced_filters)

    def _analyze_advanced_filters_fields(self, advanced_filters):
        """分析高级过滤条件中的字段类型"""
        fields_info = {
            'has_account_fields': False,
            'has_opportunity_fields': False,
            'account_fields': set(),
            'opportunity_fields': set()
        }
        
        def extract_fields_from_condition(condition):
            """递归提取条件中的字段"""
            if isinstance(condition, dict):
                # 基本过滤条件
                if 'field' in condition:
                    field_name = condition['field']
                    if field_name in self.account_field_names:
                        fields_info['has_account_fields'] = True
                        fields_info['account_fields'].add(field_name)
                    else:
                        fields_info['has_opportunity_fields'] = True
                        fields_info['opportunity_fields'].add(field_name)
                
                # 复合条件
                for key in ['AND', 'OR', 'NOT']:
                    if key in condition:
                        if key == 'NOT':
                            extract_fields_from_condition(condition[key])
                        else:
                            for sub_condition in condition[key]:
                                extract_fields_from_condition(sub_condition)
        
        extract_fields_from_condition(advanced_filters)
        
        logger.info(f"Field analysis: account_fields={fields_info['account_fields']}, opportunity_fields={fields_info['opportunity_fields']}")
        return fields_info