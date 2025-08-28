import logging
from typing import List, Literal, Optional
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from app.models.customer_document import CustomerDocument
from fastapi import APIRouter, Body, HTTPException
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page
from datetime import datetime, timedelta

from app.api.routes.crm.models import (
    Account,
    VisitRecordCreate,
    DailyReportRequest,
    DailyReportResponse,
    DailyReportStatistics,
    AssessmentDetail,
    DepartmentDailyReportResponse,
    CompanyDailyReportResponse,
    DepartmentWeeklyReportResponse,
    CompanyWeeklyReportResponse,
    WeeklyReportRequest,
    CustomerDocumentUploadRequest,
)
from app.crm.save_engine import (
    save_visit_record_to_crm_table, 
    push_visit_record_message,
    save_visit_record_with_content
)
from app.api.routes.crm.models import VisitRecordQueryRequest
from app.services.customer_document_service import CustomerDocumentService
from app.services.document_processing_service import document_processing_service
from app.services.crm_statistics_service import crm_statistics_service
from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from sqlmodel import select, or_, distinct, func
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.models.user_profile import UserProfile
from uuid import UUID


logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize view registry
view_registry = ViewRegistry()

# Initialize view engine
view_engine = CrmViewEngine(view_registry=view_registry)


@router.post("/crm/views", response_model=Page[Account])
def query_crm_view(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CrmViewRequest,
):
    try:
        # 使用 execute_view_query 获取分页数据
        result = view_engine.execute_view_query(
            db_session=db_session,
            request=request,
            user_id=user.id
        )
        
        # 转换为 Page 格式
        return Page(
            items=result["data"],
            total=result["total"],
            page=result["page"],
            size=result["page_size"],
            pages=result["total_pages"]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.get("/crm/views/fields")
async def get_view_fields(
    view_type: ViewType = ViewType.STANDARD
):
    try:
        fields = view_engine.view_registry.get_all_fields()
        return fields
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.get("/crm/views/filter-options")
async def get_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
):
    try:
        return view_engine.get_filter_options(
            db_session=db_session,
            user_id=user.id
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_record")
def create_visit_record(
    db_session: SessionDep,
    user: CurrentUserDep,
    record: VisitRecordCreate,
    force: bool = Body(False, example=False),
    feishu_auth_code: Optional[str] = Body(None, description="飞书授权码，用于换取访问令牌")
):
    """
    创建拜访记录
    支持简易版和完整版表单
    """
    try:
        if not record.visit_type:
            record.visit_type = "form"
        
        # 确保记录人ID与当前用户ID一致
        if record.recorder_id:
            try:
                recorder_id = UUID(record.recorder_id)
                if recorder_id != user.id:
                    return {"code": 400, "message": "记录人ID必须与当前用户ID一致", "data": {}}
            except ValueError:
                return {"code": 400, "message": "记录人ID格式无效，应为有效的UUID", "data": {}}
        record.recorder_id = str(user.id)

        # 根据拜访类型处理
        if record.visit_type == "link":
            if not record.visit_url:
                return {"code": 400, "message": "visit_url is required", "data": {}}
            
            # 使用通用文档处理服务
            result = document_processing_service.process_document_url(
                document_url=record.visit_url,
                user_id=str(user.id),
                auth_code=feishu_auth_code
            )
            
            # 如果处理失败，直接返回结果
            if not result.get("success"):
                # 转换响应格式以匹配拜访记录的API格式
                if result.get("data", {}).get("auth_required"):
                    data = result["data"]
                    return {
                        "code": 401,
                        "message": result["message"],
                        "data": data
                    }
                else:
                    return {
                        "code": 400,
                        "message": result["message"],
                        "data": result.get("data", {})
                    }
            
            # 处理成功，保存拜访记录和文档内容
            try:
                result_data = save_visit_record_with_content(
                    record=record,
                    content=result["content"],
                    document_type=result["document_type"],
                    user=user,
                    db_session=db_session,
                    title=result.get("title")
                )
                
                # 提交事务
                db_session.commit()                
                return result_data                
            except Exception as e:
                # 如果保存失败，回滚事务
                db_session.rollback()
                logger.error(f"Failed to save visit record: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
        # 处理 form 类型的拜访记录（包括 force 和普通保存）
        if force:
            # 直接保存，不做AI判断
            try:
                save_visit_record_to_crm_table(record, db_session)
                db_session.commit()
                # 推送飞书消息
                record_data = record.model_dump()
                # 去掉attachment字段，避免传输过大的base64编码数据
                if "attachment" in record_data:
                    del record_data["attachment"]
                
                push_visit_record_message(
                    visit_type=record.visit_type,
                    sales_visit_record=record_data,
                    db_session=db_session,
                    meeting_notes=None
                )
                return {"code": 0, "message": "success", "data": {}}
            except Exception as e:
                db_session.rollback()
                logger.error(f"Failed to save visit record with force: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
        # 根据表单类型处理数据
        from app.core.config import settings
        form_type = record.form_type or settings.CRM_VISIT_RECORD_FORM_TYPE.value

        # 使用可靠的处理函数，分组处理任务
        from app.crm.save_engine import process_visit_record_content_reliable
        
        # 根据表单类型调用可靠函数
        if form_type == "simple":
            # 简易版表单：传入followup_content
            result = process_visit_record_content_reliable(followup_content=record.followup_content)
        else:
            # 完整版表单：传入followup_record和next_steps
            result = process_visit_record_content_reliable(
                followup_record=record.followup_record,
                next_steps=record.next_steps
            )
        
        # 将处理结果赋值给record
        record.followup_record = result["followup_record"]
        record.followup_record_zh = result["followup_record_zh"]
        record.followup_record_en = result["followup_record_en"]
        record.followup_quality_level_zh = result["followup_quality_level_zh"]
        record.followup_quality_level_en = result["followup_quality_level_en"]
        record.followup_quality_reason_zh = result["followup_quality_reason_zh"]
        record.followup_quality_reason_en = result["followup_quality_reason_en"]
        record.next_steps = result["next_steps"]
        record.next_steps_zh = result["next_steps_zh"]
        record.next_steps_en = result["next_steps_en"]
        record.next_steps_quality_level_zh = result["next_steps_quality_level_zh"]
        record.next_steps_quality_level_en = result["next_steps_quality_level_en"]
        record.next_steps_quality_reason_zh = result["next_steps_quality_reason_zh"]
        record.next_steps_quality_reason_en = result["next_steps_quality_reason_en"]
        
        # 构建返回数据
        data = {
            "followup": {
                "level_zh": result["followup_quality_level_zh"], 
                "reason_zh": result["followup_quality_reason_zh"], 
                "content": result["followup_record"],
                "content_zh": result["followup_record_zh"],
                "content_en": result["followup_record_en"],
                "level_en": result["followup_quality_level_en"],
                "reason_en": result["followup_quality_reason_en"]
            },
            "next_steps": {
                "level_zh": result["next_steps_quality_level_zh"], 
                "reason_zh": result["next_steps_quality_reason_zh"], 
                "content": result["next_steps"],
                "content_zh": result["next_steps_zh"],
                "content_en": result["next_steps_en"],
                "level_en": result["next_steps_quality_level_en"],
                "reason_en": result["next_steps_quality_reason_en"]
            }
        }
        
        # 质量检查：只要有一项不合格就阻止保存
        if (result["followup_quality_level_zh"] == "不合格" or result["next_steps_quality_level_zh"] == "不合格" or 
            result["followup_quality_level_en"] == "unqualified" or result["next_steps_quality_level_en"] == "unqualified"):
            return {"code": 400, "message": "failed", "data": data}

        try:
            save_visit_record_to_crm_table(record, db_session)
            db_session.commit()
            # 推送飞书消息
            record_data = record.model_dump()
            # 去掉attachment字段，避免传输过大的base64编码数据
            if "attachment" in record_data:
                del record_data["attachment"]
            
            push_visit_record_message(
                visit_type=record.visit_type,
                sales_visit_record=record_data,
                db_session=db_session,
                meeting_notes=None
            )
            return {"code": 0, "message": "success", "data": data}
        except Exception as e:
            db_session.rollback()
            logger.error(f"Failed to save visit record after quality check: {e}")
            return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
    


@router.post("/crm/visit_record/verify")
def verify_visit_record(
    user: CurrentUserDep,
    followup_content: Optional[str] = Body(None, example=""),
    followup_record: Optional[str] = Body(None, example=""),
    next_steps: Optional[str] = Body(None, example=""),
):
    try:
        from app.crm.save_engine import process_visit_record_content_reliable
        
        # 使用统一的处理流程
        if followup_content:
            result = process_visit_record_content_reliable(followup_content=followup_content)
        else:
            result = process_visit_record_content_reliable(
                followup_record=followup_record,
                next_steps=next_steps
            )
        
        data = {
            "followup": {
                "level_zh": result["followup_quality_level_zh"],
                "reason_zh": result["followup_quality_reason_zh"],
                "content": result["followup_record"],
                "content_zh": result["followup_record_zh"],
                "content_en": result["followup_record_en"],
                "level_en": result["followup_quality_level_en"],
                "reason_en": result["followup_quality_reason_en"]
            },
            "next_steps": {
                "level_zh": result["next_steps_quality_level_zh"],
                "reason_zh": result["next_steps_quality_reason_zh"],
                "content": result["next_steps"],
                "content_zh": result["next_steps_zh"],
                "content_en": result["next_steps_en"],
                "level_en": result["next_steps_quality_level_en"],
                "reason_en": result["next_steps_quality_reason_en"]
            }
        }
        
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_records/query")
def query_visit_records(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: VisitRecordQueryRequest,
):
    """
    查询CRM拜访记录
    支持条件查询和分页
    根据当前用户的汇报关系限制数据访问权限
    """
    try:
        
        result = visit_record_repo.query_visit_records(
            session=db_session,
            request=request,
            current_user_id=user.id
        )
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "items": [item.model_dump() for item in result.items],
                "total": result.total,
                "page": result.page,
                "page_size": result.size,
                "pages": result.pages
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/visit_records/filter-options")
def get_visit_record_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
    form_type: Optional[Literal["simple", "complete"]] = None,
):
    """
    获取拜访记录查询的过滤选项
    用于前端下拉选择框等
    根据表单类型配置返回相应的字段
    """
    try:
        if not form_type:
            from app.core.config import settings
            form_type = settings.CRM_VISIT_RECORD_FORM_TYPE.value
        
        # 通用字段：无论哪种类型都返回
        # 获取客户名称选项
        account_names = db_session.exec(
            select(distinct(CRMSalesVisitRecord.account_name))
            .where(CRMSalesVisitRecord.account_name.is_not(None))
            .order_by(CRMSalesVisitRecord.account_name)
        ).all()
        
        # 获取合作伙伴选项
        partner_names = db_session.exec(
            select(distinct(CRMSalesVisitRecord.partner_name))
            .where(CRMSalesVisitRecord.partner_name.is_not(None))
            .order_by(CRMSalesVisitRecord.partner_name)
        ).all()
        
        # 获取记录人选项
        recorders = db_session.exec(
            select(distinct(CRMSalesVisitRecord.recorder))
            .where(CRMSalesVisitRecord.recorder.is_not(None))
            .order_by(CRMSalesVisitRecord.recorder)
        ).all()
        
        # 获取跟进质量等级选项（中英文）
        followup_quality_levels_zh = db_session.exec(
            select(distinct(CRMSalesVisitRecord.followup_quality_level_zh))
            .where(CRMSalesVisitRecord.followup_quality_level_zh.is_not(None))
            .order_by(CRMSalesVisitRecord.followup_quality_level_zh)
        ).all()
        
        followup_quality_levels_en = db_session.exec(
            select(distinct(CRMSalesVisitRecord.followup_quality_level_en))
            .where(CRMSalesVisitRecord.followup_quality_level_en.is_not(None))
            .order_by(CRMSalesVisitRecord.followup_quality_level_en)
        ).all()
        
        # 获取下一步计划质量等级选项（中英文）
        next_steps_quality_levels_zh = db_session.exec(
            select(distinct(CRMSalesVisitRecord.next_steps_quality_level_zh))
            .where(CRMSalesVisitRecord.next_steps_quality_level_zh.is_not(None))
            .order_by(CRMSalesVisitRecord.next_steps_quality_level_zh)
        ).all()
        
        next_steps_quality_levels_en = db_session.exec(
            select(distinct(CRMSalesVisitRecord.next_steps_quality_level_en))
            .where(CRMSalesVisitRecord.next_steps_quality_level_en.is_not(None))
            .order_by(CRMSalesVisitRecord.next_steps_quality_level_en)
        ).all()
        
        # 获取客户分类选项
        customer_levels = db_session.exec(
            select(distinct(CRMAccount.customer_level))
            .where(CRMAccount.customer_level.is_not(None))
            .order_by(CRMAccount.customer_level)
        ).all()
        
        # 获取部门选项 - 从用户档案表获取拜访人的部门
        departments = db_session.exec(
            select(distinct(UserProfile.department))
            .where(UserProfile.department.is_not(None))
            .order_by(UserProfile.department)
        ).all()
        
        # 基础返回数据
        result_data = {
            "account_names": account_names,
            "partner_names": partner_names,
            "recorders": recorders,
            "followup_quality_levels_zh": followup_quality_levels_zh,
            "followup_quality_levels_en": followup_quality_levels_en,
            "next_steps_quality_levels_zh": next_steps_quality_levels_zh,
            "next_steps_quality_levels_en": next_steps_quality_levels_en,
            "customer_levels": customer_levels,
            "departments": departments,
        }
        
        # 根据表单类型添加特定字段
        if form_type == "simple":
            # 简易版：添加拜访主题
            subjects = db_session.exec(
                select(distinct(CRMSalesVisitRecord.subject))
                .where(CRMSalesVisitRecord.subject.is_not(None))
                .order_by(CRMSalesVisitRecord.subject)
            ).all()
            result_data["subjects"] = subjects
        else:
            # 完整版：添加其他现有字段
            communication_methods = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_communication_method))
                .where(CRMSalesVisitRecord.visit_communication_method.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_communication_method)
            ).all()
            
            visit_types = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_type))
                .where(CRMSalesVisitRecord.visit_type.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_type)
            ).all()
            
            result_data.update({
                "communication_methods": communication_methods,
                "visit_types": visit_types,
            })
        
        return {
            "code": 0,
            "message": "success",
            "data": result_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/visit_records/{record_id}")
def get_visit_record_by_id(
    db_session: SessionDep,
    user: CurrentUserDep,
    record_id: int,
):
    """
    根据ID获取单个拜访记录详情
    根据当前用户的汇报关系限制数据访问权限
    """
    try:
        record = visit_record_repo.get_visit_record_by_id(
            session=db_session,
            record_id=record_id,
            current_user_id=user.id
        )
        
        if not record:
            raise HTTPException(status_code=404, detail="拜访记录不存在或无权限访问")
        
        return {
            "code": 0,
            "message": "success",
            "data": record.model_dump()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/daily-reports", response_model=Page[DailyReportResponse])
def get_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyReportRequest,
):
    """
    获取销售个人日报信息
    
    返回销售人员的日报统计数据，包括：
    - 记录人/销售人员
    - 部门名称
    - 报告日期
    - 统计数据（客户跟进、合作伙伴跟进、评估等级统计）
    - 拜访记录详情页面链接
    
    支持的查询条件：
    - sales_id: 精确匹配销售人员ID
    - sales_name: 模糊匹配销售人员姓名
    - start_date/end_date: 日期范围过滤
    - department_name: 精确匹配部门名称
    - 分页参数: page, page_size
    """
    try:
        # 如果没有指定日期范围，使用最近7天的数据
        if not request.start_date and not request.end_date:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        else:
            start_date = request.start_date or (datetime.now().date() - timedelta(days=7))
            end_date = request.end_date or datetime.now().date()
        
        # 收集所有日报数据
        all_reports = []
        current_date = start_date
        
        while current_date <= end_date:
            daily_reports = crm_statistics_service.get_complete_daily_report(
                session=db_session, 
                target_date=current_date
            )
            
            for report in daily_reports:
                # 应用过滤条件
                if request.sales_id and report.get('sales_id') != request.sales_id:
                    continue
                if request.sales_name and request.sales_name.lower() not in (report.get('sales_name', '')).lower():
                    continue
                if request.department_name and report.get('department_name') != request.department_name:
                    continue
                
                # 转换为API响应格式
                daily_report = DailyReportResponse(
                    recorder=report['sales_name'],
                    department_name=report['department_name'],
                    report_date=report['report_date'],
                    statistics=[DailyReportStatistics(
                        end_customer_total_follow_up=report.get('end_customer_total_follow_up', 0),
                        end_customer_total_first_visit=report.get('end_customer_total_first_visit', 0),
                        end_customer_total_multi_visit=report.get('end_customer_total_multi_visit', 0),
                        partner_total_follow_up=report.get('partner_total_follow_up', 0),
                        partner_total_first_visit=report.get('partner_total_first_visit', 0),
                        partner_total_multi_visit=report.get('partner_total_multi_visit', 0),
                        assessment_red_count=report.get('assessment_red_count', 0),
                        assessment_yellow_count=report.get('assessment_yellow_count', 0),
                        assessment_green_count=report.get('assessment_green_count', 0)
                    )],
                    visit_detail_page=report['visit_detail_page'],
                    account_list_page=report['account_list_page'],
                    first_assessment=[AssessmentDetail(**assessment) for assessment in report['first_assessment']],
                    multi_assessment=[AssessmentDetail(**assessment) for assessment in report['multi_assessment']]
                )
                all_reports.append(daily_report)
            
            current_date += timedelta(days=1)
        
        # 按日期降序排序
        all_reports.sort(key=lambda x: x.report_date, reverse=True)
        
        # 手动分页
        total = len(all_reports)
        start_idx = (request.page - 1) * request.page_size
        end_idx = start_idx + request.page_size
        paginated_reports = all_reports[start_idx:end_idx]
        
        total_pages = (total + request.page_size - 1) // request.page_size
        
        return Page(
            items=paginated_reports,
            total=total,
            page=request.page,
            size=request.page_size,
            pages=total_pages
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/crm/daily-reports/filter-options")
def get_daily_report_filter_options(
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """
    获取销售个人日报的过滤选项
    用于前端下拉选择框等
    """
    try:
        # 获取销售人员选项
        sales_names = db_session.exec(
            select(distinct(CRMDailyAccountStatistics.sales_name))
            .where(CRMDailyAccountStatistics.sales_name.is_not(None))
            .order_by(CRMDailyAccountStatistics.sales_name)
        ).all()
        
        # 获取部门选项
        department_names = db_session.exec(
            select(distinct(CRMDailyAccountStatistics.department_name))
            .where(CRMDailyAccountStatistics.department_name.is_not(None))
            .order_by(CRMDailyAccountStatistics.department_name)
        ).all()
        
        # 获取日期范围
        date_range = db_session.exec(
            select(
                func.min(CRMDailyAccountStatistics.report_date),
                func.max(CRMDailyAccountStatistics.report_date)
            )
        ).first()
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "sales_names": sales_names,
                "department_names": department_names,
                "date_range": {
                    "min_date": date_range[0].isoformat() if date_range[0] else None,
                    "max_date": date_range[1].isoformat() if date_range[1] else None,
                }
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.post("/crm/department-daily-reports", response_model=List[DepartmentDailyReportResponse])
def get_department_daily_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取部门日报数据
    
    按部门汇总销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并
    3. 包含各销售的详细日报信息
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        部门日报数据列表
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的部门日报数据")
        
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_reports(
            session=db_session,
            target_date=parsed_date
        )
        
        if not department_reports:
            logger.warning(f"{parsed_date} 没有找到任何部门日报数据")
            return []
        
        # 转换为响应格式
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_date'), 'isoformat'):
                report['report_date'] = report['report_date'].isoformat()
            
            # 构造响应对象
            department_response = DepartmentDailyReportResponse(**report)
            response_reports.append(department_response)
        
        logger.info(f"成功返回 {len(response_reports)} 个部门的日报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取部门日报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/company-daily-report", response_model=CompanyDailyReportResponse)
def get_company_daily_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天")
):
    """
    获取公司日报数据
    
    汇总全公司销售人员的日报数据：
    1. 统计数据直接加和
    2. 评估数据直接合并（但不包含跟进记录字段，且过滤绿灯评估）
    3. 提供公司级的整体数据概览
    
    注意：推送逻辑根据应用环境区分
    - 内部环境：推送到匹配的群聊
    - 外部环境：推送给外部管理员
    
    Args:
        target_date: 目标日期，格式YYYY-MM-DD
        
    Returns:
        公司日报数据
    """
    try:
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="日期格式错误，请使用YYYY-MM-DD格式")
        else:
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 查询 {parsed_date} 的公司日报数据")
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_report(
            session=db_session,
            target_date=parsed_date
        )
        
        if not company_report:
            logger.warning(f"{parsed_date} 没有找到任何公司日报数据")
            raise HTTPException(status_code=400, detail=f"{parsed_date} 没有找到任何数据")
        
        # 转换日期格式
        if hasattr(company_report.get('report_date'), 'isoformat'):
            company_report['report_date'] = company_report['report_date'].isoformat()
        
        # 构造响应对象
        company_response = CompanyDailyReportResponse(**company_report)
        
        logger.info(f"成功返回公司日报数据")
        return company_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取公司日报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/department-weekly-reports", response_model=List[DepartmentWeeklyReportResponse])
def get_department_weekly_reports(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取团队周报数据
    
    按部门汇总销售人员的周报数据：
    1. 统计数据直接加和
    2. 计算平均值（总跟进数除以销售人员数量）
    3. 提供周报相关的页面链接
    
    Args:
        request: 周报查询请求，包含部门名称和日期范围
        
    Returns:
        团队周报数据列表
    """
    try:
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的团队周报数据")
        
        # 获取部门汇总报告
        department_reports = crm_statistics_service.aggregate_department_weekly_reports(
            session=db_session,
            start_date=start_date,
            end_date=end_date
        )
        
        if not department_reports:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何团队周报数据")
            return []
        
        # 如果指定了部门名称，进行过滤
        if request.department_name:
            department_reports = [
                report for report in department_reports 
                if report.get('department_name') == request.department_name
            ]
        
        # 转换为响应格式
        response_reports = []
        for report in department_reports:
            # 转换日期格式
            if hasattr(report.get('report_start_date'), 'isoformat'):
                report['report_start_date'] = report['report_start_date'].isoformat()
            if hasattr(report.get('report_end_date'), 'isoformat'):
                report['report_end_date'] = report['report_end_date'].isoformat()
            
            # 构造响应对象
            department_response = DepartmentWeeklyReportResponse(**report)
            response_reports.append(department_response)
        
        logger.info(f"成功返回 {len(response_reports)} 个团队的周报数据")
        return response_reports
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取团队周报数据失败: {e}")
        raise InternalServerError()


@router.post("/crm/company-weekly-report", response_model=CompanyWeeklyReportResponse)
def get_company_weekly_report(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: WeeklyReportRequest,
):
    """
    获取公司周报数据
    
    汇总所有部门的周报数据：
    1. 统计数据直接加和
    2. 计算平均值（总跟进数除以公司所有销售人员数量）
    3. 提供周报相关的页面链接
    
    Args:
        request: 周报查询请求，包含日期范围
        
    Returns:
        公司周报数据
    """
    try:
        # 解析日期范围
        if request.start_date and request.end_date:
            start_date = request.start_date
            end_date = request.end_date
        else:
            # 默认查询最近一周的数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=7)
        
        logger.info(f"用户 {user.id} 查询 {start_date} 到 {end_date} 的公司周报数据")
        
        # 获取公司汇总报告
        company_report = crm_statistics_service.aggregate_company_weekly_report(
            session=db_session,
            start_date=start_date,
            end_date=end_date
        )
        
        if not company_report:
            logger.warning(f"{start_date} 到 {end_date} 没有找到任何公司周报数据")
            return None
        
        logger.info(f"成功生成公司周报数据，日期范围: {start_date} 到 {end_date}")
        
        return company_report
        
    except Exception as e:
        logger.exception(f"查询公司周报数据失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"查询公司周报数据失败: {str(e)}"
        )


@router.post("/crm/customer-document/upload")
def upload_customer_document(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: CustomerDocumentUploadRequest,
):
    """
    上传客户文档
    
    支持飞书文档链接和本地文件路径，自动处理授权和内容读取。
    
    Args:
        request: 文档上传请求，包含文件类别、客户信息、文档链接等。
        
    Returns:
        文档上传响应，包含上传结果、文档ID或授权信息等。
    """
    try:
        customer_document_service = CustomerDocumentService()
        
        # 处理uploader_id类型转换和验证
        uploader_id = request.uploader_id
        if uploader_id:
            try:
                uploader_id = UUID(uploader_id)
                # 确保上传者ID与当前用户ID一致
                if uploader_id != user.id:
                    return {"code": 400, "message": "上传者ID必须与当前用户ID一致", "data": {}}
            except ValueError:
                return {"code": 400, "message": "uploader_id格式无效，应为有效的UUID", "data": {}}
        else:
            uploader_id = user.id
        
        # 上传客户文档
        result = customer_document_service.upload_customer_document(
            db_session=db_session,
            file_category=request.file_category,
            account_name=request.account_name,
            account_id=request.account_id,
            document_url=request.document_url,
            uploader_id=uploader_id,
            uploader_name=request.uploader_name or user.name or user.email,
            feishu_auth_code=request.feishu_auth_code
        )
        
        # 如果上传成功
        if result.get("success"):
            return {
                "code": 0,
                "message": "success",
                "data": {}
            }
        
        # 如果需要授权，返回401状态码
        if result.get("data", {}).get("auth_required"):
            data = result["data"]
            return {
                "code": 401,
                "message": result["message"],
                "data": data
            }
        
        # 其他错误情况
        return {
            "code": 400,
            "message": result["message"],
            "data": result.get("data", {})
        }
        
    except Exception as e:
        logger.exception(f"上传客户文档失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"上传客户文档失败: {str(e)}"
        )


@router.get("/crm/customer-documents")
def get_customer_documents(
    db_session: SessionDep,
    user: CurrentUserDep,
    account_id: Optional[str] = None,
    file_category: Optional[str] = None,
    uploader_id: Optional[str] = None,
    view_type: Optional[str] = "auto",  # auto, my, team, all
):
    """
    获取客户文档列表（根据用户权限自动过滤）
    
    权限规则：
    - 普通用户：只能查看自己上传的文档
    - 团队lead：可以查看本团队的所有文档
    - 超级管理员或管理员：可以查看所有文档
    
    Args:
        account_id: 客户ID（可选）
        file_category: 文件类别（可选）
        uploader_id: 上传者ID（可选，仅超级管理员或管理员可用）
        view_type: 视图类型
            - "auto": 根据用户权限自动选择（默认）
            - "my": 只查看自己的文档
            - "team": 查看团队文档（仅团队lead和超管可用）
            - "all": 查看所有文档（仅超管可用）
        
    Returns:
        根据权限过滤的客户文档列表
    """
    try:
        customer_document_service = CustomerDocumentService()
        user_profile_repo = UserProfileRepo()
        
        # 获取当前用户的部门信息
        user_profile = user_profile_repo.get_by_oauth_user_id(db_session, str(user.id))
        current_user_department = user_profile.department if user_profile else None
        
        # 检查是否为团队lead（没有直属上级且有部门名称的用户被认为是leader）
        is_team_lead = user_profile and not user_profile.direct_manager_id and user_profile.department
        
        # 检查是否为超级管理员或管理员
        is_superuser_or_admin = customer_document_service._is_superuser_or_admin(
            db_session=db_session,
            user_id=user.id,
            user_is_superuser=user.is_superuser,
            user_profile=user_profile
        )
        
        # 根据view_type和用户权限确定查询范围
        if view_type == "my":
            # 强制查看自己的文档
            documents = customer_document_service.get_customer_documents(
                db_session=db_session,
                uploader_id=str(user.id),
                file_category=file_category
            )
            user_role = "user"
            view_description = "我的文档"
            
        elif view_type == "team":
            # 查看团队文档
            if not is_team_lead and not is_superuser_or_admin:
                raise HTTPException(
                    status_code=403,
                    detail="只有团队lead和超级管理员可以查看团队文档"
                )
            
            if is_superuser_or_admin:
                # 管理员可以查看所有文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    account_id=account_id,
                    file_category=file_category,
                    uploader_id=uploader_id
                )
                view_description = "所有团队文档"
            else:
                # 团队lead查看本团队文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if not team_member_ids:
                    documents = []
                else:
                    statement = select(CustomerDocument).where(
                        or_(*[CustomerDocument.uploader_id == member_id for member_id in team_member_ids])
                    )
                    
                    if account_id:
                        statement = statement.where(CustomerDocument.account_id == account_id)
                    if file_category:
                        statement = statement.where(CustomerDocument.file_category == file_category)
                    
                    statement = statement.order_by(CustomerDocument.created_at.desc())
                    
                    documents = db_session.exec(statement).all()
                
                view_description = f"{current_user_department}团队文档"
            user_role = "team_lead" if is_team_lead else "superuser_or_admin"
            
        elif view_type == "all":
            # 查看所有文档（仅超管可用）
            if not is_superuser_or_admin:
                raise HTTPException(
                    status_code=403,
                    detail="只有超级管理员或管理员可以查看所有文档"
                )
            
            documents = customer_document_service.get_customer_documents(
                db_session=db_session,
                account_id=account_id,
                file_category=file_category,
                uploader_id=uploader_id
            )
            user_role = "superuser_or_admin"
            view_description = "所有文档"
            
        else:  # view_type == "auto" 或默认
            # 根据用户权限自动选择
            if is_superuser_or_admin:
                # 超管默认查看所有文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    account_id=account_id,
                    file_category=file_category,
                    uploader_id=uploader_id
                )
                user_role = "superuser_or_admin"
                view_description = "所有文档"
                
            elif is_team_lead:
                # 团队lead默认查看本团队文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if not team_member_ids:
                    documents = []
                else:
                    statement = select(CustomerDocument).where(
                        or_(*[CustomerDocument.uploader_id == member_id for member_id in team_member_ids])
                    )
                    
                    if account_id:
                        statement = statement.where(CustomerDocument.account_id == account_id)
                    if file_category:
                        statement = statement.where(CustomerDocument.file_category == file_category)
                    
                    statement = statement.order_by(CustomerDocument.created_at.desc())
                    
                    documents = db_session.exec(statement).all()
                
                user_role = "team_lead"
                view_description = f"{current_user_department}团队文档"
                
            else:
                # 普通用户默认查看自己的文档
                documents = customer_document_service.get_customer_documents(
                    db_session=db_session,
                    uploader_id=user.id,
                    file_category=file_category
                )
                user_role = "user"
                view_description = "我的文档"
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "documents": [
                    {
                        "id": doc.id,
                        "file_category": doc.file_category,
                        "account_name": doc.account_name,
                        "account_id": doc.account_id,
                        "document_url": doc.document_url,
                        "document_type": doc.document_type,
                        "document_title": doc.document_title,
                        "uploader_id": doc.uploader_id,
                        "uploader_name": doc.uploader_name,
                        "created_at": doc.created_at.isoformat(),
                        "updated_at": doc.updated_at.isoformat()
                    }
                    for doc in documents
                ],
                "total": len(documents),
                "user_role": user_role,
                "view_type": view_type,
                "view_description": view_description,
                "team_department": current_user_department if is_team_lead else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取客户文档列表失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"获取客户文档列表失败: {str(e)}"
        )


@router.get("/crm/customer-documents/{document_id}")
def get_customer_document(
    db_session: SessionDep,
    user: CurrentUserDep,
    document_id: int,
):
    """
    获取客户文档详情（根据用户权限）
    
    - 普通用户：只能查看自己上传的文档
    - 团队lead：可以查看本团队的所有文档
    - 超级管理员：可以查看所有文档
    
    Args:
        document_id: 文档ID
        
    Returns:
        客户文档详情
    """
    try:
        customer_document_service = CustomerDocumentService()
        user_profile_repo = UserProfileRepo()
        
        # 获取文档详情
        document = customer_document_service.get_customer_document_by_id(
            db_session=db_session,
            document_id=document_id
        )
        
        if not document:
            raise HTTPException(
                status_code=404,
                detail="文档不存在"
            )
        
        # 获取当前用户的部门信息
        user_profile = user_profile_repo.get_by_oauth_user_id(db_session, str(user.id))
        current_user_department = user_profile.department if user_profile else None
        
        # 权限检查
        # 检查是否为超级管理员或管理员
        is_superuser_or_admin = customer_document_service._is_superuser_or_admin(
            db_session=db_session,
            user_id=user.id,
            user_is_superuser=user.is_superuser,
            user_profile=user_profile
        )
        
        if is_superuser_or_admin:
            # 超级管理员或管理员可以查看所有文档
            pass
        else:
            # 检查是否为团队lead（没有直属上级且有部门名称的用户被认为是leader）
            is_team_lead = user_profile and not user_profile.direct_manager_id and user_profile.department
            
            if is_team_lead:
                # 团队lead可以查看本团队的文档
                team_members = user_profile_repo.get_department_members(db_session, current_user_department)
                team_member_ids = [str(member.oauth_user_id) for member in team_members if member.oauth_user_id]
                
                if document.uploader_id not in team_member_ids:
                    raise HTTPException(
                        status_code=403,
                        detail="无权访问此文档"
                    )
            else:
                # 普通用户只能查看自己上传的文档
                if document.uploader_id != user.id:
                    raise HTTPException(
                        status_code=403,
                        detail="无权访问此文档"
                    )
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "id": document.id,
                "file_category": document.file_category,
                "account_name": document.account_name,
                "account_id": document.account_id,
                "document_url": document.document_url,
                "document_type": document.document_type,
                "document_title": document.document_title,
                "uploader_id": document.uploader_id,
                "uploader_name": document.uploader_name,
                "created_at": document.created_at.isoformat(),
                "updated_at": document.updated_at.isoformat(),
                "document_content_id": document.document_content_id
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取客户文档详情失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"获取客户文档详情失败: {str(e)}"
        )


