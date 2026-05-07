"""CRM 拜访记录与日客户跟进 HTTP 路由。"""

import hashlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Literal, Optional
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlmodel import distinct, select

from app.api.deps import CurrentUserDep, SessionDep
from app.api.routes.crm.models import (
    DailyCustomerFollowupQueryRequest,
    RecordType,
    VisitRecordCommentsUpdate,
    VisitRecordCreate,
    VisitRecordQueryRequest,
)
from app.crm.save_engine import (
    push_visit_record_message,
    save_visit_record_to_crm_table,
    save_visit_record_with_content,
)
from app.exceptions import InternalServerError
from app.models.crm_accounts import CRMAccount
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.user_profile import UserProfile
from app.repositories.crm_account_opportunity_assessment import crm_account_opportunity_assessment_repo
from app.repositories.document_content import DocumentContentRepo
from app.repositories.user_profile import UserProfileRepo
from app.repositories.visit_record import visit_record_repo
from app.services.document_processing_service import document_processing_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/visit-records"])


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
                if not user.is_superuser and recorder_id != user.id:
                    return {"code": 400, "message": "记录人ID必须与当前用户ID一致", "data": {}}
                # 验证通过后，确保recorder_id为标准格式的UUID字符串
                record.recorder_id = str(recorder_id)
            except ValueError:
                return {"code": 400, "message": "记录人ID格式无效，应为有效的UUID", "data": {}}
        else:
            logger.info(f"Fill in recorder id with current user id: {user.id}")
            record.recorder_id = str(user.id)

        # if not record.recorder or record.recorder == '未知用户':
        logger.info(f"Fill in recorder name with recorder id: {record.recorder_id}")
        user_profile = UserProfileRepo().get_by_recorder_id(db_session, record.recorder_id)
        logger.info(f"User profile: {user_profile}")
        if user_profile:
            record.recorder = user_profile.name
            logger.info(f"Filled in recorder name: {user_profile.name}")
        else:
            logger.warning(f"Could not find user profile for recorder_id: {record.recorder_id}, use payload recorder name: {record.recorder}")
            record.recorder = record.recorder or '未知用户'

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
                record_id, saved_time = save_visit_record_to_crm_table(record, db_session)
                db_session.commit()
                # 推送飞书消息（attachment 由下游统一做瘦身与解析）
                record_data = record.model_dump()
                push_visit_record_message(
                    record_id=record_id,
                    visit_type=record.visit_type,
                    sales_visit_record=record_data,
                    db_session=db_session,
                    meeting_notes=None,
                    risk_info=None,
                    saved_time=saved_time
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
            record_id, saved_time = save_visit_record_to_crm_table(record, db_session)
            db_session.commit()
            # 推送飞书消息（attachment 由下游统一做瘦身与解析）
            record_data = record.model_dump()
            push_visit_record_message(
                record_id=record_id,
                visit_type=record.visit_type,
                sales_visit_record=record_data,
                db_session=db_session,
                meeting_notes=None,
                risk_info=None,
                saved_time=saved_time
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
    db_session: SessionDep,
    user: CurrentUserDep,
    followup_content: Optional[str] = Body(None, example=""),
    followup_record: Optional[str] = Body(None, example=""),
    next_steps: Optional[str] = Body(None, example=""),
    remarks: Optional[str] = Body(None, example=""),
    visit_communication_method: Optional[str] = Body(None, example=""),
):
    try:
        from app.crm.save_engine import (
            process_visit_record_content_reliable,
            extract_risk_info_from_content,
            extract_visit_method_from_content,
        )

        input_remarks = (remarks or "").strip()
        input_visit_method = (visit_communication_method or "").strip()
        result: dict
        extracted_risk_info = ""
        extracted_visit_method = ""
        # 构造风险抽取正文
        if followup_content and followup_content.strip():
            risk_content = followup_content.strip()
        else:
            complete_parts = []
            if followup_record and followup_record.strip():
                complete_parts.append(f"跟进记录：\n{followup_record.strip()}")
            if next_steps and next_steps.strip():
                complete_parts.append(f"下一步计划：\n{next_steps.strip()}")
            risk_content = "\n\n".join(complete_parts).strip()

        # 质量评估始终执行；仅当 remarks 为空时抽取风险（与拜访方式：仅当未填时抽取 一致）
        with ThreadPoolExecutor(max_workers=3) as executor:
            quality_future = executor.submit(
                process_visit_record_content_reliable,
                followup_content=followup_content,
                followup_record=followup_record,
                next_steps=next_steps,
            )
            risk_future = None
            if not input_remarks:
                risk_future = executor.submit(
                    extract_risk_info_from_content,
                    content=risk_content,
                    title="销售拜访记录",
                    remarks=None,
                )
            visit_method_future = None
            if not input_visit_method:
                visit_method_future = executor.submit(extract_visit_method_from_content, risk_content, db_session)
            result = quality_future.result()
            if risk_future:
                extracted_risk_info = (risk_future.result() or "").strip()
            if visit_method_future:
                extracted_visit_method = (visit_method_future.result() or "").strip()

        if input_remarks:
            ai_remarks = input_remarks
        else:
            ai_remarks = f"[SIA提取] {extracted_risk_info}" if extracted_risk_info else ""
        final_visit_method = input_visit_method or extracted_visit_method

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
            },
            "remarks": ai_remarks,
            "visit_communication_method": final_visit_method
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


@router.post("/crm/daily_customer_followups/query")
def query_daily_customer_followups(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: DailyCustomerFollowupQueryRequest,
):
    """
    查询每日客户跟进（红黄绿灯评估明细）
    基于 crm_account_opportunity_assessment，按条件筛选并分页返回。
    """
    try:
        _ = user
        response = crm_account_opportunity_assessment_repo.query_daily_customer_followups(
            session=db_session,
            request=request,
        )
        return {
            "code": 0,
            "message": "success",
            "data": response.model_dump(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_records/export")
def export_visit_records_to_xlsx(
    db_session: SessionDep,
    user: CurrentUserDep,
    request: VisitRecordQueryRequest,
):
    """
    导出CRM拜访记录到 XLSX 文件
    支持条件查询和分页
    根据当前用户的汇报关系限制数据访问权限
    支持中英文版本导出
    """
    try:
        # 创建 XLSX 内容
        wb = Workbook()
        ws = wb.active
        ws.title = "visit_records"
        
        # 根据语言参数确定表头和数据内容
        language = request.language or "zh"  # 默认为中文
        
        if language == "en":
            # 英文版表头 - 只包含英文字段
            headers = [
                "ID", "Customer Level", "Account Name", "Account ID", "First Visit", "Call High",
                "Partner Name", "Partner ID", "Opportunity Name", "Opportunity ID", "Follow-up Date", "Person in Charge", "Department",
                "Contact Position", "Contact Name", "Collaborative Participants", "Follow-up Method",
                "Visit Purpose", "Attachment Location", "Attachment Latitude", "Attachment Longitude", "Attachment Taken At", "Follow-up Record", 
                "AI Follow-up Record Quality Evaluation", "AI Follow-up Record Quality Evaluation Details", 
                "Next Steps", "AI Next Steps Quality Evaluation", "AI Next Steps Quality Evaluation Details",
                "Assessment Flag", "Record Type", "Information Source", "Remarks", "Created Time"
            ]
        else:
            # 中文版表头（默认）- 只包含中文字段
            headers = [
                "ID", "客户分类", "客户名称", "客户ID", "是否首次拜访", "是否Call High",
                "合作伙伴", "合作伙伴ID", "商机名称", "商机ID", "跟进日期", "负责销售", "所在团队",
                "联系人职位", "联系人姓名", "协同参与人", "跟进方式",
                "拜访目的", "附件地点", "附件纬度", "附件经度", "附件拍摄时间", "跟进记录", 
                "AI对跟进记录质量评估", "AI对跟进记录质量评估详情",
                "下一步计划", "AI对下一步计划质量评估", "AI对下一步计划质量评估详情",
                "评估标记", "记录类型", "信息来源", "备注", "创建时间"
            ]
        
        ws.append(headers)
        
        # 使用分页查询循环获取所有数据
        # 限制最大导出10000条记录
        # 如果用户指定了page_size且大于0，则使用用户指定的值（但不超过10000）
        # 否则默认导出最多10000条
        if request.page_size and request.page_size > 0:
            max_export_count = min(request.page_size, 10000)
        else:
            max_export_count = 10000
        page_size = 100  # 每次查询100条（fastapi_pagination的限制）
        current_page = 1
        total_exported = 0
        total_pages = 0
        
        # 辅助函数：将单个item转换为表格行
        def item_to_row(item):
            # 根据语言选择对应的字段值
            is_en = language == "en"
            
            # 生成基于关键字段的hash ID
            # 使用客户名称、跟进日期、负责销售等关键字段生成唯一ID
            # 处理联系人：优先使用contacts字段，否则使用旧字段
            contact_names_str = ""
            if item.contacts and len(item.contacts) > 0:
                contact_names_str = ", ".join([c.name or "" for c in item.contacts if c.name])
            else:
                contact_names_str = item.contact_name or ""
            
            key_fields = [
                str(item.id or ""),
                str(item.account_name or item.partner_name or item.opportunity_name or ""),
                str(item.visit_communication_date or ""),
                str(item.recorder or ""),
                contact_names_str,
                str(item.last_modified_time or ""),
            ]
            key_string = "|".join(key_fields)
            record_id = hashlib.md5(key_string.encode('utf-8')).hexdigest()[:12]  # 取前12位作为ID
            
            # 布尔值字段的本地化处理
            first_visit_text = "Yes" if item.is_first_visit else "No" if item.is_first_visit is not None else ""
            call_high_text = "Yes" if item.is_call_high else "No" if item.is_call_high is not None else ""
            if not is_en:
                first_visit_text = "是" if item.is_first_visit else "否" if item.is_first_visit is not None else ""
                call_high_text = "是" if item.is_call_high else "否" if item.is_call_high is not None else ""
            
            # 多语言字段的本地化处理
            followup_record = item.followup_record_en if is_en else item.followup_record_zh
            followup_record = followup_record or item.followup_record or ""
            
            followup_quality_level = item.followup_quality_level_en if is_en else item.followup_quality_level_zh or ""
            followup_quality_reason = item.followup_quality_reason_en if is_en else item.followup_quality_reason_zh or ""
            
            next_steps = item.next_steps_en if is_en else item.next_steps_zh
            next_steps = next_steps or item.next_steps or ""
            
            next_steps_quality_level = item.next_steps_quality_level_en if is_en else item.next_steps_quality_level_zh or ""
            next_steps_quality_reason = item.next_steps_quality_reason_en if is_en else item.next_steps_quality_reason_zh or ""

            # 评估标记统一导出为表情符号
            raw_assessment_flag = str(item.assessment_flag or "").strip()
            assessment_flag_map = {
                "red": "🔴",
                "yellow": "🟡",
                "green": "🟢"
            }
            assessment_flag = assessment_flag_map.get(raw_assessment_flag.lower(), raw_assessment_flag)
            
            # 处理记录类型字段的多语言显示
            record_type = ""
            if item.record_type:
                record_type_enum = RecordType.from_english(item.record_type)
                if record_type_enum:
                    record_type = record_type_enum.english if is_en else record_type_enum.chinese
                else:
                    record_type = item.record_type
            
            # 从附件中解析位置信息和经纬度
            attachment = getattr(item, "attachment", None)
            if attachment:
                # 结构化附件（VisitAttachment）
                location = getattr(attachment, "location", None) or ""
                latitude = getattr(attachment, "latitude", None) or ""
                longitude = getattr(attachment, "longitude", None) or ""
                taken_at = getattr(attachment, "taken_at", None) or ""
            else:
                location = ""
                latitude = ""
                longitude = ""
                taken_at = ""
            # 处理联系人信息：优先使用contacts字段，否则使用旧字段
            contact_positions_str = ""
            contact_names_str = ""
            if item.contacts and len(item.contacts) > 0:
                # 多个联系人：格式化为 "职位1, 职位2" 和 "姓名1, 姓名2"
                positions = [c.position or "" for c in item.contacts if c.position]
                names = [c.name or "" for c in item.contacts if c.name]
                contact_positions_str = ", ".join(positions)
                contact_names_str = ", ".join(names)
            else:
                # 兼容旧数据：使用单个联系人字段
                contact_positions_str = item.contact_position or ""
                contact_names_str = item.contact_name or ""
            
            # 构建数据行（中英版本字段顺序相同，ID列在最前面）
            return [
                item.record_id or record_id,
                item.customer_level or "",
                item.account_name or "",
                item.account_id or "",
                first_visit_text,
                call_high_text,
                item.partner_name or "",
                item.partner_id or "",
                item.opportunity_name or "",
                item.opportunity_id or "",
                item.visit_communication_date or "",
                item.recorder or "",
                item.department or "",
                contact_positions_str,
                contact_names_str,
                item.collaborative_participants or "",
                item.visit_communication_method or "",
                item.visit_purpose or "",
                location,
                latitude,
                longitude,
                taken_at,
                followup_record,
                followup_quality_level,
                followup_quality_reason,
                next_steps,
                next_steps_quality_level,
                next_steps_quality_reason,
                assessment_flag,
                record_type,
                item.visit_type or "",
                item.remarks or "",
                item.last_modified_time or ""
            ]
        
        # 循环分页查询并写入数据
        while total_exported < max_export_count:
            # 查询当前页
            export_request = request.model_copy()
            export_request.page = current_page
            export_request.page_size = page_size
            
            result = visit_record_repo.query_visit_records(
                session=db_session,
                request=export_request,
                current_user_id=user.id
            )
            
            # 第一次查询时，获取总数和总页数
            if current_page == 1:
                # 计算需要查询的总页数（不超过最大导出数量）
                total_pages = min((max_export_count + page_size - 1) // page_size, result.pages)
            
            # 如果没有数据，退出循环
            if not result.items:
                break
            
            # 写入当前页的数据
            for item in result.items:
                if total_exported >= max_export_count:
                    break
                ws.append(item_to_row(item))
                total_exported += 1
            
            # 如果当前页数据不足一页，说明已经是最后一页
            if len(result.items) < page_size:
                break
            
            # 如果已经达到需要查询的总页数，退出循环
            if current_page >= total_pages:
                break
            
            current_page += 1
        
        # 准备文件下载
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        # 生成文件名（包含语言标识）
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        language_suffix = "_en" if language == "en" else "_zh"
        filename = f"visit_records_export{language_suffix}_{current_time}.xlsx"
        
        # 创建响应
        def iter_xlsx():
            yield output.getvalue()
        
        return StreamingResponse(
            iter_xlsx(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            }
        )
        
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
            
            visit_purposes = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_purpose))
                .where(CRMSalesVisitRecord.visit_purpose.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_purpose)
            ).all()
            
            visit_types = db_session.exec(
                select(distinct(CRMSalesVisitRecord.visit_type))
                .where(CRMSalesVisitRecord.visit_type.is_not(None))
                .order_by(CRMSalesVisitRecord.visit_type)
            ).all()
            
            result_data.update({
                "communication_methods": communication_methods,
                "visit_purposes": visit_purposes,
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
    record_id: str,
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
        
        # 基础数据
        data = record.model_dump()

        # 如果是 link 类型的拜访记录，尝试返回从文档中抽取的问答对和风险信息
        try:
            if getattr(record, "visit_type", None) == "link":
                document_content_repo = DocumentContentRepo()
                # visit_record_id 在 DocumentContent 中对应的是 CRM 表里的 record_id 字段
                visit_record_id = getattr(record, "record_id", None)
                if visit_record_id:
                    document_content = document_content_repo.get_by_visit_record_id(
                        session=db_session,
                        visit_record_id=visit_record_id,
                    )
                    if document_content:
                        data["document_qa_pairs"] = document_content.qa_pairs or []
                        data["document_qa_extract_status"] = document_content.qa_extract_status or ""
                        data["document_risk_info"] = document_content.risk_info or ""
                        data["document_risk_extract_status"] = document_content.risk_extract_status or ""
        except Exception as e:
            # 文档信息加载失败不影响主流程，只记录日志
            logger.warning(f"加载文档信息（问答对和风险信息）失败: record_id={record_id}, error={e}")
        
        return {
            "code": 0,
            "message": "success",
            "data": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/crm/visit_records/{record_id}/comments")
def update_visit_record_comments(
    db_session: SessionDep,
    user: CurrentUserDep,
    record_id: str,
    payload: VisitRecordCommentsUpdate,
):
    """
    保存指定拜访记录的评论（comments，JSON数组）
    - 复用拜访记录的权限控制逻辑：无权限/不存在返回 404
    """
    try:
        logger.info(
            "update_visit_record_comments start: record_id=%s, user_id=%s, payload_comments_count=%s",
            record_id,
            str(getattr(user, "id", "") or ""),
            len(payload.comments or []),
        )
        updated_record = visit_record_repo.update_visit_record_comments(
            session=db_session,
            record_id=record_id,
            comments=[c.model_dump() for c in (payload.comments or [])],
            current_user_id=user.id
        )

        if updated_record is None:
            raise HTTPException(status_code=404, detail="拜访记录不存在或无权限访问")

        # 保存评论成功后：推送提醒给拜访记录的记录人（不影响主流程，失败仅记录日志）
        try:
            record = updated_record
            recipient_user_id = str(getattr(record, "recorder_id", "") or "")
            current_user_id = str(getattr(user, "id", "") or "")
            logger.info(
                "update_visit_record_comments saved: record_id=%s, current_user_id=%s, recipient_user_id=%s, saved_comments_count=%s",
                record_id,
                current_user_id,
                recipient_user_id,
                len(getattr(record, "comments", None) or []),
            )

            if record and recipient_user_id and recipient_user_id != current_user_id:
                # 以落库后的 comments 为准，避免 payload 最后一条与实际落库不一致
                comments_saved = getattr(record, "comments", None)
                comments_list = comments_saved if isinstance(comments_saved, list) else []

                # 只取当前用户写入的最新一条评论，避免用他人评论或空列表误推送
                latest_comment = None
                for item in reversed(comments_list):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("author_id") or "") == current_user_id:
                        latest_comment = item
                        break

                latest_comment_type = ""
                if isinstance(latest_comment, dict):
                    latest_comment_type = str(latest_comment.get("type") or "").strip().lower()
                logger.info(
                    "visit_record_comment notify candidate: record_id=%s, has_latest_comment=%s, latest_comment_type=%s, latest_comment_author=%s, latest_comment_content_preview=%s",
                    record_id,
                    latest_comment is not None,
                    latest_comment_type or "",
                    str((latest_comment or {}).get("author") or "") if isinstance(latest_comment, dict) else "",
                    (
                        str((latest_comment or {}).get("content") or "")[:80]
                        if isinstance(latest_comment, dict)
                        else ""
                    ),
                )
                # 仅当存在当前用户的新评论且不是 task 时才推送
                if latest_comment is not None and latest_comment_type != "task":
                    from app.core.config import settings

                    # 评论摘要（允许为空）
                    comment_preview = ""
                    if isinstance(latest_comment, dict):
                        comment_preview = str((latest_comment.get("content") or "")).strip()
                    if len(comment_preview) > 200:
                        comment_preview = comment_preview[:197] + "..."

                    # 跳转到拜访记录评论页
                    jump_url = f"{settings.REVIEW_REPORT_HOST}/registerVisitRecord/detail?record_id={record_id}"

                    author_name = ""
                    if isinstance(latest_comment, dict):
                        author_name = str(latest_comment.get("author") or "").strip()
                    # 不依赖前端传 author；优先使用当前登录用户信息
                    author_name = author_name or str(getattr(user, "name", "") or "").strip()
                    author_name = author_name or "有人"

                    title = (getattr(record, "account_name", None) or getattr(record, "partner_name", None) or "") or ""
                    opp = (getattr(record, "opportunity_name", None) or "") or ""
                    link_text = f"{title}  {opp}".strip() or "拜访记录"

                    text = (
                        f"{author_name}评论了你的拜访记录\n"
                        f"[{link_text}]({jump_url})\n"
                        f"评论：{comment_preview or '--'}\n"
                    )

                    from app.services.platform_notification_service import platform_notification_service
                    platform_notification_service.send_visit_record_comment_notification(
                        db_session,
                        recipient_user_id=recipient_user_id,
                        message_text=text,
                    )
                    logger.info(
                        "visit_record_comment notify sent: record_id=%s, recipient_user_id=%s, author_name=%s",
                        record_id,
                        recipient_user_id,
                        author_name,
                    )
                else:
                    logger.info(
                        "visit_record_comment notify skipped: record_id=%s, reason=%s",
                        record_id,
                        "no_latest_comment_for_current_user"
                        if latest_comment is None
                        else "latest_comment_is_task",
                    )
            else:
                logger.info(
                    "visit_record_comment notify skipped: record_id=%s, reason=%s",
                    record_id,
                    "invalid_recipient_or_self_notification",
                )
        except Exception as e:
            logger.warning(f"发送拜访记录评论提醒失败（不影响保存评论）：{e}")

        return {"code": 0, "message": "success", "data": {"comments": updated_record.comments or []}}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


