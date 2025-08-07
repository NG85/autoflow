from typing import Optional, List
from datetime import datetime, date
import logging
from sqlmodel import Session, select, func, and_, or_, desc, asc, text
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.api.routes.crm.models import VisitRecordQueryRequest, VisitRecordResponse
from app.repositories.base_repo import BaseRepo

logger = logging.getLogger(__name__)


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

    def query_visit_records(
        self,
        session: Session,
        request: VisitRecordQueryRequest,
    ) -> Page[VisitRecordResponse]:
        """
        查询拜访记录，支持条件过滤和分页
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
    ) -> Optional[VisitRecordResponse]:
        """
        根据ID获取单个拜访记录
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

        result = session.exec(query).first()
        if not result:
            return None

        record, customer_level, department, person_in_charge = result
        return _convert_to_response(record, customer_level, department, person_in_charge)


# 创建repository实例
visit_record_repo = VisitRecordRepo()
