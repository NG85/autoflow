import logging
from typing import List, Optional
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from app.models.customer_document import CustomerDocument
from fastapi import APIRouter, Body, HTTPException
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page

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
    CustomerDocumentUploadResponse
)
from app.crm.save_engine import (
    save_visit_record_to_crm_table, 
    check_followup_quality, 
    check_next_steps_quality, 
    push_visit_record_feishu_message,
    save_visit_record_with_content
)
from app.api.routes.crm.models import VisitRecordQueryRequest
from app.services.customer_document_service import CustomerDocumentService
from app.services.document_processing_service import DocumentProcessingService
from app.repositories.user_profile import UserProfileRepo
from sqlmodel import select, or_
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
            document_processing_service = DocumentProcessingService()
            result = document_processing_service.process_document_url(
                document_url=record.visit_url,
                user_id=str(user.id),
                feishu_auth_code=feishu_auth_code
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
                return save_visit_record_with_content(
                    record=record,
                    content=result["content"],
                    document_type=result["document_type"],
                    user=user,
                    db_session=db_session,
                    title=result.get("title")
                )
            except Exception as e:
                # 如果保存失败，回滚事务
                db_session.rollback()
                logger.error(f"Failed to save visit record: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
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
                
                push_visit_record_feishu_message(
                    visit_type=record.visit_type,
                    sales_visit_record=record_data,
                    db_session=db_session
                )
                return {"code": 0, "message": "success", "data": {}}
            except Exception as e:
                db_session.rollback()
                logger.error(f"Failed to save visit record with force: {e}")
                return {"code": 400, "message": "保存拜访记录失败，请重试", "data": {}}
        
        # 评估跟进质量
        followup_quality_level, followup_quality_reason = check_followup_quality(record.followup_record)
        next_steps_quality_level, next_steps_quality_reason = check_next_steps_quality(record.next_steps)
        data = {
            "followup": {"level": followup_quality_level, "reason": followup_quality_reason},
            "next_steps": {"level": next_steps_quality_level, "reason": next_steps_quality_reason}
        }
        # 只要有一项不合格就阻止保存
        if followup_quality_level == "不合格" or next_steps_quality_level == "不合格":
            return {"code": 400, "message": "failed", "data": data}

        record.followup_quality_level = followup_quality_level
        record.followup_quality_reason = followup_quality_reason
        record.next_steps_quality_level = next_steps_quality_level
        record.next_steps_quality_reason = next_steps_quality_reason
        try:
            save_visit_record_to_crm_table(record, db_session)
            db_session.commit()
            # 推送飞书消息
            record_data = record.model_dump()
            # 去掉attachment字段，避免传输过大的base64编码数据
            if "attachment" in record_data:
                del record_data["attachment"]
            
            push_visit_record_feishu_message(
                visit_type=record.visit_type,
                sales_visit_record=record_data,
                db_session=db_session
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
    followup_record: str = Body(..., example=""),
    next_steps: str = Body(..., example=""),
):
    try:
        followup_quality_level, followup_quality_reason = check_followup_quality(followup_record)
        next_steps_quality_level, next_steps_quality_reason = check_next_steps_quality(next_steps)
        data = {
            "followup": {"level": followup_quality_level, "reason": followup_quality_reason},
            "next_steps": {"level": next_steps_quality_level, "reason": next_steps_quality_reason}
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
        from app.repositories.visit_record import visit_record_repo
        
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
):
    """
    获取拜访记录查询的过滤选项
    用于前端下拉选择框等
    """
    try:
        from sqlmodel import select, func, distinct, text
        from app.models.crm_sales_visit_records import CRMSalesVisitRecord
        from app.models.crm_accounts import CRMAccount
        from app.models.user_profile import UserProfile
        
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
        
        # 获取跟进方式选项
        communication_methods = db_session.exec(
            select(distinct(CRMSalesVisitRecord.visit_communication_method))
            .where(CRMSalesVisitRecord.visit_communication_method.is_not(None))
            .order_by(CRMSalesVisitRecord.visit_communication_method)
        ).all()
        
        # 获取跟进质量等级选项
        followup_quality_levels = db_session.exec(
            select(distinct(CRMSalesVisitRecord.followup_quality_level))
            .where(CRMSalesVisitRecord.followup_quality_level.is_not(None))
            .order_by(CRMSalesVisitRecord.followup_quality_level)
        ).all()
        
        # 获取下一步计划质量等级选项
        next_steps_quality_levels = db_session.exec(
            select(distinct(CRMSalesVisitRecord.next_steps_quality_level))
            .where(CRMSalesVisitRecord.next_steps_quality_level.is_not(None))
            .order_by(CRMSalesVisitRecord.next_steps_quality_level)
        ).all()
        
        # 获取拜访类型选项
        visit_types = db_session.exec(
            select(distinct(CRMSalesVisitRecord.visit_type))
            .where(CRMSalesVisitRecord.visit_type.is_not(None))
            .order_by(CRMSalesVisitRecord.visit_type)
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
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "account_names": account_names,
                "partner_names": partner_names,
                "recorders": recorders,
                "communication_methods": communication_methods,
                "followup_quality_levels": followup_quality_levels,
                "next_steps_quality_levels": next_steps_quality_levels,
                "visit_types": visit_types,
                "customer_levels": customer_levels,
                "departments": departments,
            }
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
        from app.repositories.visit_record import visit_record_repo
        
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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
            daily_reports = crm_daily_statistics_service.get_complete_daily_report(
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
        from sqlmodel import select, distinct, func
        from app.models.crm_daily_account_statistics import CRMDailyAccountStatistics
        
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



@router.post("/crm/daily-reports/trigger-task")
def trigger_daily_statistics_task(
    user: CurrentUserDep,
    target_date: Optional[str] = Body(None, description="目标日期，格式YYYY-MM-DD，不传则默认为昨天"),
    enable_feishu_push: Optional[bool] = Body(None, description="是否启用飞书推送，不传则使用系统配置")
):
    """
    手动触发CRM日报统计定时任务
    
    直接调用Celery任务，与定时任务执行相同的逻辑：
    1. 从crm_daily_account_statistics表查询销售统计数据
    2. 通过correlation_id关联crm_account_assessment表获取评估详情
    3. 组合成完整的日报数据
    4. 向每个销售人员本人推送个人日报飞书卡片
    
    注意：
    - 这是异步任务，会立即返回任务ID，可通过任务ID查询执行状态
    - 个人日报只推送给销售人员本人，不会推送给上级或群聊
    """
    try:
        from app.tasks.cron_jobs import generate_crm_daily_statistics
        from datetime import datetime, timedelta
        
        # 解析目标日期
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                return {
                    "code": 400,
                    "message": "日期格式错误，请使用YYYY-MM-DD格式",
                    "data": {}
                }
        else:
            # 默认为昨天
            parsed_date = (datetime.now() - timedelta(days=1)).date()
        
        logger.info(f"用户 {user.id} 手动触发CRM日报统计任务，目标日期: {parsed_date}")
        
        # 如果用户指定了飞书推送设置，需要临时修改配置
        if enable_feishu_push is not None:
            logger.info(f"用户指定飞书推送设置: {enable_feishu_push}")
        
        # 触发异步任务，传递日期参数
        task = generate_crm_daily_statistics.delay(target_date_str=parsed_date.isoformat())
        
        return {
            "code": 0,
            "message": "CRM日报统计任务已触发",
            "data": {
                "task_id": task.id,
                "target_date": parsed_date.isoformat(),
                "status": "PENDING",
                "description": f"已提交 {parsed_date} 的CRM日报统计任务到队列，任务ID: {task.id}"
            }
        }
        
    except Exception as e:
        logger.exception(f"触发CRM日报统计任务失败: {e}")
        return {
            "code": 500,
            "message": f"触发任务失败: {str(e)}",
            "data": {}
        }


@router.post("/crm/weekly-reports/trigger-task")
def trigger_weekly_report_task(
    user: CurrentUserDep,
    start_date: Optional[str] = Body(None, description="开始日期，格式YYYY-MM-DD，不传则默认为上周一"),
    end_date: Optional[str] = Body(None, description="结束日期，格式YYYY-MM-DD，不传则默认为上周日"),
    enable_feishu_push: Optional[bool] = Body(None, description="是否启用飞书推送，不传则使用系统配置")
):
    """
    手动触发CRM周报推送任务
    
    用于测试或手动执行周报推送功能
    """
    if not user.is_superuser:
        return {
            "code": 403,
            "message": "权限不足，只有超级管理员可以触发此任务",
            "data": {}
        }

    try:
        from app.tasks.cron_jobs import generate_crm_weekly_report
        from datetime import datetime, timedelta
        
        # 解析日期范围
        if start_date and end_date:
            try:
                parsed_start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                parsed_end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            except ValueError:
                return {
                    "code": 400,
                    "message": "日期格式错误，请使用YYYY-MM-DD格式",
                    "data": {}
                }
        else:
            # 默认处理上周的数据
            today = datetime.now().date()
            # 计算上周一（今天往前推7天，然后找到最近的周一）
            days_since_monday = today.weekday()
            last_monday = today - timedelta(days=days_since_monday + 7)
            last_sunday = last_monday + timedelta(days=6)
            
            parsed_start_date = last_monday
            parsed_end_date = last_sunday
        
        logger.info(f"用户 {user.id} 手动触发CRM周报推送任务，日期范围: {parsed_start_date} 到 {parsed_end_date}")
        
        # 如果用户指定了飞书推送设置，需要临时修改配置
        if enable_feishu_push is not None:
            logger.info(f"用户指定飞书推送设置: {enable_feishu_push}")
        
        # 触发异步任务，传递日期参数
        task = generate_crm_weekly_report.delay(
            start_date_str=parsed_start_date.isoformat(),
            end_date_str=parsed_end_date.isoformat()
        )
        
        return {
            "code": 0,
            "message": "CRM周报推送任务已触发",
            "data": {
                "task_id": task.id,
                "start_date": parsed_start_date.isoformat(),
                "end_date": parsed_end_date.isoformat(),
                "status": "PENDING",
                "description": f"已提交 {parsed_start_date} 到 {parsed_end_date} 的CRM周报推送任务到队列，任务ID: {task.id}"
            }
        }
        
    except Exception as e:
        logger.exception(f"触发CRM周报推送任务失败: {e}")
        return {
            "code": 500,
            "message": f"触发任务失败: {str(e)}",
            "data": {}
        }


@router.get("/crm/daily-reports/task-status/{task_id}")
def get_task_status(
    task_id: str,
    user: CurrentUserDep,
):
    """
    查询CRM日报统计任务的执行状态
    
    Args:
        task_id: 任务ID（由trigger-task接口返回）
        
    Returns:
        任务状态信息
    """
    try:
        from app.celery import app
        
        # 获取任务状态
        task_result = app.AsyncResult(task_id)
        
        status = task_result.status
        result = task_result.result
        
        response_data = {
            "task_id": task_id,
            "status": status,
        }
        
        if status == "PENDING":
            response_data["message"] = "任务正在等待执行"
        elif status == "SUCCESS":
            response_data["message"] = "任务执行成功"
            response_data["result"] = result
        elif status == "FAILURE":
            response_data["message"] = "任务执行失败"
            response_data["error"] = str(result) if result else "未知错误"
        elif status == "RETRY":
            response_data["message"] = "任务正在重试"
        else:
            response_data["message"] = f"任务状态: {status}"
        
        return {
            "code": 0,
            "message": "success",
            "data": response_data
        }
        
    except Exception as e:
        logger.exception(f"查询任务状态失败: {e}")
        return {
            "code": 500,
            "message": f"查询任务状态失败: {str(e)}",
            "data": {}
        }


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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
        department_reports = crm_daily_statistics_service.aggregate_department_reports(
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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
        company_report = crm_daily_statistics_service.aggregate_company_report(
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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
        department_reports = crm_daily_statistics_service.aggregate_department_weekly_reports(
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
        from app.services.crm_daily_statistics_service import crm_daily_statistics_service
        from datetime import datetime, timedelta
        
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
        company_report = crm_daily_statistics_service.aggregate_company_weekly_report(
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


@router.post("/crm/customer-document/upload", response_model=CustomerDocumentUploadResponse)
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
            return CustomerDocumentUploadResponse(
                success=True,
                message=result["message"],
                document_id=result.get("document_id")
            )
        
        # 如果需要授权
        if result.get("data", {}).get("auth_required"):
            data = result["data"]
            return CustomerDocumentUploadResponse(
                success=False,
                message=result["message"],
                auth_required=True,
                auth_url=data.get("auth_url"),
                auth_expired=data.get("auth_expired", False),
                auth_error=data.get("auth_error", False),
                channel=data.get("channel"),
                document_type=data.get("document_type")
            )
        
        # 其他错误情况
        return CustomerDocumentUploadResponse(
            success=False,
            message=result["message"]
        )
        
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


