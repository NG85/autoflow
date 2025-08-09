import logging
from typing import List, Optional
import os
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError, BadRequest
from fastapi import APIRouter, Body, HTTPException
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page

from app.api.routes.crm.models import Account, VisitRecordCreate, DailyReportRequest, DailyReportResponse, DailyReportStatistics, AssessmentDetail, DepartmentDailyReportResponse, CompanyDailyReportResponse
from app.crm.save_engine import (
    save_visit_record_to_crm_table, 
    check_followup_quality, 
    check_next_steps_quality, 
    push_visit_record_feishu_message,
    save_visit_record_with_content
)
from app.feishu.oauth_service import FeishuOAuthService
from app.feishu.common_open import (
    get_content_from_feishu_source_with_token,
    UnsupportedDocumentTypeError,
    parse_feishu_url,
    check_document_type_support
)
from app.core.config import settings
from app.crm.file_processor import get_file_content_from_local_storage
from app.api.routes.crm.models import VisitRecordQueryRequest


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
    external: bool = Body(False, example=False),
    force: bool = Body(False, example=False),
    feishu_auth_code: Optional[str] = Body(None, description="飞书授权码，用于换取访问令牌")
):
    try:
        if not record.visit_type:
            record.visit_type = "form"

        # 根据拜访类型处理
        if record.visit_type == "link":
            if not record.visit_url:
                return {"code": 400, "message": "visit_url is required", "data": {}}
            
            # 检查是否为飞书链接
            if 'feishu.cn' in record.visit_url or 'larksuite.com' in record.visit_url:                
                # 检查文档类型是否支持
                try:
                    url_type, doc_token = parse_feishu_url(record.visit_url)
                    if not url_type or not doc_token:
                        return {"code": 400, "message": "无法解析飞书URL", "data": {}}
                    
                    check_document_type_support(url_type, record.visit_url)
                except UnsupportedDocumentTypeError as e:
                    return {"code": 400, "message": str(e), "data": {"unsupported_type": True}}
                
                # 辅助函数：生成scope和授权URL
                def generate_auth_response(message: str, auth_expired: bool = False, auth_error: bool = False):
                    scope_parts = []
                    
                    if url_type in ['doc', 'docx']:
                        scope_parts.append("docx:document:readonly")
                        scope_parts.append("docs:document.content:read")
                    elif url_type == 'minutes':
                        scope_parts.append("minutes:minutes:readonly")
                        scope_parts.append("minutes:minutes.transcript:export")
                    elif url_type == 'wiki_node':
                        scope_parts.append("wiki:wiki:readonly")
                        scope_parts.append("wiki:node:read")
                    
                    scope = " ".join(scope_parts)
                    
                    auth_url = oauth_service.generate_auth_url(
                        scope=scope,
                        # redirect_uri='http://localhost:8000/api/v1'
                    )
                    
                    data = {
                        "auth_required": True,
                        "auth_url": auth_url,
                        "channel": "feishu",
                        "url": record.visit_url,
                        "document_type": url_type
                    }
                    
                    if auth_expired:
                        data["auth_expired"] = True
                    if auth_error:
                        data["auth_error"] = True
                    
                    return {
                        "code": 401,
                        "message": message,
                        "data": data
                    }
                
                # 首先尝试从Redis获取用户的飞书access token
                oauth_service = FeishuOAuthService()
                logger.debug(f"app_id: {oauth_service.app_id}, app_secret: {oauth_service.app_secret}")
                feishu_access_token = oauth_service.get_access_token_from_redis(str(user.id), url_type)
                
                # 如果Redis中没有token，且提供了授权码，则换取token
                if not feishu_access_token and feishu_auth_code:
                    try:
                        success, message, access_token = oauth_service.exchange_code_for_token(feishu_auth_code, str(user.id), url_type)
                        
                        if not success or not access_token:
                            # 授权码失败（可能过期），生成新的授权URL
                            logger.warning(f"Auth code exchange failed: {message}, generating new auth URL")
                            return generate_auth_response("授权码已过期，需要重新授权", auth_expired=True)
                        
                        feishu_access_token = access_token
                        logger.info(f"Successfully exchanged auth code for token: {access_token}")
                        
                    except Exception as e:
                        logger.error(f"Failed to exchange auth code for token: {e}")
                        # 异常情况下也生成授权URL
                        return generate_auth_response("授权处理异常，需要重新授权", auth_error=True)
                
                # 如果没有提供飞书access token，生成授权URL
                if not feishu_access_token:
                    return generate_auth_response("需要飞书授权才能访问该链接")
                
                # 有access token，尝试获取内容
                try:
                    original_content, document_type = get_content_from_feishu_source_with_token(record.visit_url, feishu_access_token)
                    
                    if not original_content:
                        return {"code": 400, "message": "未获取到飞书内容，请检查链接或重新授权", "data": {}}
                    
                    # 保存拜访记录和文档内容
                    return save_visit_record_with_content(
                        record=record,
                        content=original_content,
                        document_type=document_type,
                        user=user,
                        db_session=db_session,
                        external=external
                    )
                        
                except UnsupportedDocumentTypeError as e:
                    # 返回不支持的文档类型错误
                    return {"code": 400, "message": str(e), "data": {"unsupported_type": True}}
                except Exception as e:
                    logger.error(f"Failed to get feishu content: {e}")
                    return {"code": 400, "message": "获取飞书内容失败，请检查授权或重试", "data": {}}
            else:
                # 暂不支持除飞书外的其他网络链接，以及非本地挂载的存储路径
                if record.visit_url.startswith("http") or not record.visit_url.startswith(settings.STORAGE_PATH_PREFIX):
                    return {"code": 400, "message": "不支持的文件链接", "data": {}}
                
                # 处理文件路径 data/customer-uploads/XXX.docx -> /shared/data/customer-uploads/XXX.docx
                record.visit_url = record.visit_url.replace('data', settings.LOCAL_FILE_STORAGE_PATH)
                logger.info(f"Non-feishu URL detected, should be customer uploaded file: {record.visit_url}")
                try:
                    # 获取文件内容
                    file_content, document_type = get_file_content_from_local_storage(record.visit_url)
                    
                    if not file_content:
                        return {"code": 400, "message": "未获取到文件内容，请检查后重试", "data": {}}
                    
                    # 保存拜访记录和文档内容
                    return save_visit_record_with_content(
                        record=record,
                        content=file_content,
                        document_type=document_type,
                        user=user,
                        db_session=db_session,
                        external=external,
                        title=os.path.basename(record.visit_url)
                    )
                    
                except Exception as e:
                    # 如果保存失败，回滚事务
                    db_session.rollback()
                    logger.error(f"Failed to save non-feishu visit record: {e}")
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
                    external=external,
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
                external=external,
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
                        parter_total_follow_up=report.get('parter_total_follow_up', 0),
                        parter_total_first_visit=report.get('parter_total_first_visit', 0),
                        parter_total_multi_visit=report.get('parter_total_multi_visit', 0),
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
                raise BadRequest("日期格式错误，请使用YYYY-MM-DD格式")
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
                raise BadRequest("日期格式错误，请使用YYYY-MM-DD格式")
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
            raise BadRequest(f"{parsed_date} 没有找到任何数据")
        
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