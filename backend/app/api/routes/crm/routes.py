import logging
from typing import Optional
import os
from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from fastapi import APIRouter, Body, HTTPException
from app.crm.view_engine import CrmViewRequest, ViewType, CrmViewEngine, ViewRegistry
from fastapi_pagination import Page

from app.api.routes.crm.models import Account, VisitRecordCreate
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
                push_visit_record_feishu_message(
                    external=external,
                    visit_type=record.visit_type,
                    sales_visit_record={
                        **record.model_dump()
                    },
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
            push_visit_record_feishu_message(
                external=external,
                visit_type=record.visit_type,
                sales_visit_record={
                    **record.model_dump()
                },
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
    """
    try:
        from app.repositories.visit_record import visit_record_repo
        
        result = visit_record_repo.query_visit_records(
            session=db_session,
            request=request
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
        
        # 获取部门选项
        departments = db_session.exec(
            select(distinct(CRMAccount.department))
            .where(CRMAccount.department.is_not(None))
            .order_by(CRMAccount.department)
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
    """
    try:
        from app.repositories.visit_record import visit_record_repo
        
        record = visit_record_repo.get_visit_record_by_id(
            session=db_session,
            record_id=record_id
        )
        
        if not record:
            raise HTTPException(status_code=404, detail="拜访记录不存在")
        
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