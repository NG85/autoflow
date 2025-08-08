from typing import Optional, List
from datetime import datetime, date
import logging
from sqlmodel import Session, select, func, and_, or_, desc, asc, text
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate
from uuid import UUID

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.api.routes.crm.models import VisitRecordQueryRequest, VisitRecordResponse
from app.repositories.base_repo import BaseRepo
from app.repositories.user_profile import UserProfileRepo

logger = logging.getLogger(__name__)

# 管理团队配置
DEFAULT_EXTERNAL_EXTENDED_ADMINS = [
    {
        "name": "龙恒",
        "email": "ls@pingcap.cn",
        "open_id": "ou_adcaafc471d57fc6f9b209c05c0f5ce1",
        "user_id": "01971c23-28be-70de-a08c-6e58e0911491"
    },
    {
        "name": "林微",
        "email": "wei.lin@pingcap.cn",
        "open_id": "ou_edbdc2e3fc8eb411bbc49cc586629709",
        "user_id": "0196d251-3fa0-71f8-91d3-9a03a412c954"
    },
    {
        "name": "崔秋",
        "email": "cuiqiu@pingcap.cn",
        "open_id": "ou_718d03819e549537c4dc972154798a81",
        "user_id": "019739bd-4be4-79a5-92d8-f2fb470b10c8"
    },
]


def _convert_to_response(record: CRMSalesVisitRecord, customer_level: Optional[str] = None, department: Optional[str] = None, person_in_charge: Optional[str] = None) -> VisitRecordResponse:
    """
    将CRMSalesVisitRecord转换为VisitRecordResponse
    """
    # 先处理需要转换的字段
    record_dict = record.model_dump()
    
    # 处理UUID字段转换为字符串
    if record.recorder_id:
        record_dict["recorder_id"] = str(record.recorder_id)
    
    # 处理日期字段转换为ISO格式字符串
    if record.visit_communication_date:
        record_dict["visit_communication_date"] = record.visit_communication_date.isoformat()
    if record.last_modified_time:
        record_dict["last_modified_time"] = record.last_modified_time.isoformat()
    
    # 使用处理后的字典创建VisitRecordResponse
    response = VisitRecordResponse.model_validate(record_dict)
    
    # 添加关联字段
    response.customer_level = customer_level
    response.department = department
    response.person_in_charge = person_in_charge
    return response


class VisitRecordRepo(BaseRepo):
    model_cls = CRMSalesVisitRecord

    def _is_admin_user(self, current_user_id: UUID) -> bool:
        """
        检查当前用户是否为管理团队成员
        """
        user_id_str = str(current_user_id)
        for admin in DEFAULT_EXTERNAL_EXTENDED_ADMINS:
            if admin.get("user_id") == user_id_str:
                return True
        return False

    def _get_user_accessible_recorder_ids(self, session: Session, current_user_id: UUID) -> List[str]:
        """
        获取当前用户可访问的记录人ID列表
        包括：当前用户自己 + 所有下属
        """
        user_profile_repo = UserProfileRepo()
        
        # 1. 获取当前用户的档案
        current_user_profile = user_profile_repo.get_by_user_id(session, current_user_id)
        
        accessible_recorder_ids = []
        
        if current_user_profile:
            # 2. 添加当前用户自己（如果有recorder_id）
            if current_user_profile.oauth_user_id:
                accessible_recorder_ids.append(current_user_profile.oauth_user_id)
            
            # 3. 获取所有下属
            subordinates = user_profile_repo.get_subordinates(session, current_user_profile.oauth_user_id or str(current_user_id))
            for subordinate in subordinates:
                if subordinate.oauth_user_id:
                    accessible_recorder_ids.append(subordinate.oauth_user_id)
        else:
            # 如果找不到用户档案，只允许查看自己的记录
            # 这里需要根据实际情况调整，可能需要从其他表获取用户信息
            logger.warning(f"No user profile found for user_id: {current_user_id}")
        
        return accessible_recorder_ids

    def query_visit_records(
        self,
        session: Session,
        request: VisitRecordQueryRequest,
        current_user_id: Optional[UUID] = None,
    ) -> Page[VisitRecordResponse]:
        """
        查询拜访记录，支持条件过滤和分页
        根据当前用户的汇报关系限制数据访问权限
        """
        # 验证分页参数
        if request.page < 1:
            request.page = 1
        if request.page_size < 1:
            request.page_size = 20
        elif request.page_size > 100:  # 限制最大页面大小
            request.page_size = 100
            
        # 构建基础查询，关联客户表获取客户分类、部门信息和负责人
        query = (
            select(
                CRMSalesVisitRecord,
                CRMAccount.customer_level,
                CRMAccount.department,
                CRMAccount.person_in_charge
            )
            .outerjoin(
                CRMAccount,
                CRMSalesVisitRecord.account_id == CRMAccount.unique_id
            )
        )

        # 应用权限控制 - 用户只能查看自己及下属的拜访记录
        if current_user_id:
            # 检查是否为管理团队成员
            if self._is_admin_user(current_user_id):
                logger.info(f"Admin user {current_user_id} detected, skipping access control")
                # 管理团队成员可以查看所有记录
            else:
                accessible_recorder_ids = self._get_user_accessible_recorder_ids(session, current_user_id)
                if accessible_recorder_ids:
                    # 根据recorder_id过滤
                    query = query.where(CRMSalesVisitRecord.recorder_id.in_(accessible_recorder_ids))
                else:
                    # 如果没有可访问的记录人，返回空结果
                    logger.warning(f"No accessible recorder IDs for user: {current_user_id}")
                    return Page(
                        items=[],
                        total=0,
                        page=request.page,
                        size=request.page_size,
                        pages=0
                    )
        else:
            # 如果没有提供用户ID，记录警告但不限制访问（向后兼容）
            logger.warning("No current_user_id provided, skipping access control")

        # 应用过滤条件
        if request.customer_level:
            query = query.where(
                CRMAccount.customer_level.in_(request.customer_level)
            )

        if request.account_id:
            query = query.where(
                CRMSalesVisitRecord.account_id.in_(request.account_id)
            )

        if request.account_name:
            # 对客户名称进行模糊检索，支持多选
            account_name_conditions = []
            for account_name in request.account_name:
                account_name_conditions.append(
                    CRMSalesVisitRecord.account_name.ilike(f"%{account_name}%")
                )
            query = query.where(or_(*account_name_conditions))

        if request.partner_name:
            # 对合作伙伴进行模糊检索，支持多选
            partner_conditions = []
            for partner in request.partner_name:
                partner_conditions.append(
                    CRMSalesVisitRecord.partner_name.ilike(f"%{partner}%")
                )
            query = query.where(or_(*partner_conditions))

        if request.visit_communication_date_start:
            try:
                start_date = datetime.strptime(request.visit_communication_date_start, "%Y-%m-%d").date()
                query = query.where(CRMSalesVisitRecord.visit_communication_date >= start_date)
            except ValueError:
                pass  # 忽略无效日期格式

        if request.visit_communication_date_end:
            try:
                end_date = datetime.strptime(request.visit_communication_date_end, "%Y-%m-%d").date()
                query = query.where(CRMSalesVisitRecord.visit_communication_date <= end_date)
            except ValueError:
                pass  # 忽略无效日期格式

        if request.recorder:
            query = query.where(
                CRMSalesVisitRecord.recorder.in_(request.recorder)
            )

        # 使用客户表的department字段作为所在团队
        if request.department:
            query = query.where(
                CRMAccount.department.in_(request.department)
            )

        if request.visit_communication_method:
            query = query.where(
                CRMSalesVisitRecord.visit_communication_method.in_(request.visit_communication_method)
            )

        if request.followup_quality_level:
            query = query.where(
                CRMSalesVisitRecord.followup_quality_level.in_(request.followup_quality_level)
            )

        if request.next_steps_quality_level:
            query = query.where(
                CRMSalesVisitRecord.next_steps_quality_level.in_(request.next_steps_quality_level)
            )

        if request.visit_type:
            query = query.where(
                CRMSalesVisitRecord.visit_type.in_(request.visit_type)
            )

        if request.is_first_visit is not None:
            query = query.where(
                CRMSalesVisitRecord.is_first_visit == request.is_first_visit
            )

        # 应用排序 - 默认按拜访日期降序
        sort_field = getattr(CRMSalesVisitRecord, request.sort_by, CRMSalesVisitRecord.visit_communication_date)
        if request.sort_direction.lower() == "desc":
            query = query.order_by(desc(sort_field))
        else:
            query = query.order_by(asc(sort_field))

        # 执行分页查询
        params = Params(page=request.page, size=request.page_size)
        result = paginate(session, query, params)

        # 转换结果格式 - 复用现有模型
        items = [_convert_to_response(record, customer_level, department, person_in_charge) 
                for record, customer_level, department, person_in_charge in result.items]

        # 返回自定义分页结果
        return Page(
            items=items,
            total=result.total,
            page=result.page,
            size=result.size,
            pages=result.pages
        )

    def get_visit_record_by_id(
        self,
        session: Session,
        record_id: int,
        current_user_id: Optional[UUID] = None,
    ) -> Optional[VisitRecordResponse]:
        """
        根据ID获取单个拜访记录
        根据当前用户的汇报关系限制数据访问权限
        """
        query = (
            select(
                CRMSalesVisitRecord,
                CRMAccount.customer_level,
                CRMAccount.department,
                CRMAccount.person_in_charge
            )
            .outerjoin(
                CRMAccount,
                CRMSalesVisitRecord.account_id == CRMAccount.unique_id
            )
            .where(CRMSalesVisitRecord.id == record_id)
        )

        # 应用权限控制
        if current_user_id:
            # 检查是否为管理团队成员
            if self._is_admin_user(current_user_id):
                logger.info(f"Admin user {current_user_id} detected, skipping access control")
                # 管理团队成员可以查看所有记录
            else:
                accessible_recorder_ids = self._get_user_accessible_recorder_ids(session, current_user_id)
                if accessible_recorder_ids:
                    query = query.where(CRMSalesVisitRecord.recorder_id.in_(accessible_recorder_ids))
                else:
                    # 如果没有可访问的记录人，返回None
                    logger.warning(f"No accessible recorder IDs for user: {current_user_id}")
                    return None
        else:
            # 如果没有提供用户ID，记录警告但不限制访问（向后兼容）
            logger.warning("No current_user_id provided, skipping access control")

        result = session.exec(query).first()
        
        if result:
            record, customer_level, department, person_in_charge = result
            return _convert_to_response(record, customer_level, department, person_in_charge)
        
        return None


# 创建repository实例
visit_record_repo = VisitRecordRepo()
