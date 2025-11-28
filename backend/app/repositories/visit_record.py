from typing import Optional, List
from datetime import datetime
import logging
from sqlmodel import Session, select, func, or_, desc, asc, String
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.crm_accounts import CRMAccount
from app.models.user_profile import UserProfile
from app.api.routes.crm.models import VisitRecordQueryRequest, VisitRecordResponse
from app.repositories.base_repo import BaseRepo
from app.repositories.user_profile import UserProfileRepo

logger = logging.getLogger(__name__)


def _convert_beijing_date_to_utc_range(beijing_date_str: str, is_start: bool = True) -> Optional[datetime]:
    """
    将北京时间的日期字符串转换为UTC时间
    
    Args:
        beijing_date_str: 北京时间的日期字符串，格式为 "YYYY-MM-DD"
        is_start: True表示开始时间（00:00:00），False表示结束时间（23:59:59）
        
    Returns:
        UTC时间对象，如果解析失败则返回None
    """
    try:
        # 解析北京时间的日期
        beijing_date = datetime.strptime(beijing_date_str, "%Y-%m-%d").date()
        
        # 根据is_start参数选择时间
        if is_start:
            # 开始时间：00:00:00
            beijing_datetime = datetime.combine(beijing_date, datetime.min.time())
        else:
            # 结束时间：23:59:59
            beijing_datetime = datetime.combine(beijing_date, datetime.max.time().replace(microsecond=0))
        
        # 转换为UTC时间
        beijing_tz = ZoneInfo("Asia/Shanghai")
        utc_tz = ZoneInfo("UTC")
        beijing_datetime = beijing_datetime.replace(tzinfo=beijing_tz)
        utc_datetime = beijing_datetime.astimezone(utc_tz)
        
        return utc_datetime
    except ValueError:
        logger.warning(f"Invalid date format: {beijing_date_str}")
        return None



def _convert_to_response(record: CRMSalesVisitRecord, customer_level: Optional[str] = None, department: Optional[str] = None) -> VisitRecordResponse:
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
        # 将UTC时间转换为本地时区字符串
        from app.utils.date_utils import convert_utc_to_local_timezone
        record_dict["last_modified_time"] = convert_utc_to_local_timezone(record.last_modified_time)
    
    # 处理协同参与人字段 - 将JSON数组转换为拼接的name字符串
    from app.utils.participants_utils import format_collaborative_participants_names
    record_dict["collaborative_participants"] = format_collaborative_participants_names(record.collaborative_participants)
    
    # 使用处理后的字典创建VisitRecordResponse
    response = VisitRecordResponse.model_validate(record_dict)
    
    # 添加关联字段
    response.customer_level = customer_level
    response.department = department  # 拜访人部门
    
    return response


class VisitRecordRepo(BaseRepo):
    model_cls = CRMSalesVisitRecord

    def _is_admin_user(self, current_user_id: UUID, session: Session) -> bool:
        """
        检查当前用户是否为拜访记录的管理团队成员
        基于用户profile中的notification_tags字段判断是否包含list_visit_records权限
        """
        user_profile_repo = UserProfileRepo()
        user_profile = user_profile_repo.get_by_user_id(session, current_user_id)
        
        if user_profile and user_profile.notification_tags:
            # 检查notification_tags中是否包含list_visit_records权限
            notification_tags = user_profile.get_notification_tags()
            if "list_visit_records" in notification_tags:
                logger.info(f"User {current_user_id} has list_visit_records permission in notification_tags: {notification_tags}")
                return True
        
        # 如果没有找到用户档案或没有相应权限，返回False
        logger.info(f"User {current_user_id} does not have list_visit_records permission")
        return False

    def _get_user_accessible_recorder_ids(self, session: Session, current_user_id: UUID) -> List[str]:
        """
        获取当前用户可访问的记录人ID列表
        包括：当前用户自己 + 所有汇报关系（递归获取所有下属） + 如果是部门负责人则包括全部门
        """
        user_profile_repo = UserProfileRepo()
        
        # 1. 获取当前用户的档案
        current_user_profile = user_profile_repo.get_by_user_id(session, current_user_id)
        
        accessible_recorder_ids = []
        
        if current_user_profile:
            # 2. 添加当前用户自己（如果有recorder_id）
            if current_user_profile.oauth_user_id:
                accessible_recorder_ids.append(current_user_profile.oauth_user_id)
            
            # 3. 递归获取所有汇报关系（包括直接下属和间接下属）
            if current_user_profile.oauth_user_id:
                all_subordinates = user_profile_repo.get_all_subordinates_recursive(session, current_user_profile.oauth_user_id)
                for subordinate in all_subordinates:
                    if subordinate.oauth_user_id:
                        accessible_recorder_ids.append(subordinate.oauth_user_id)
            
            # 4. 如果是部门负责人（没有直属上级），获取全部门所有成员
            if current_user_profile.department and not current_user_profile.direct_manager_id:
                logger.info(f"User {current_user_id} is department manager for {current_user_profile.department}, getting all department members")
                department_members = user_profile_repo.get_department_members(session, current_user_profile.department)
                for member in department_members:
                    if member.oauth_user_id and member.oauth_user_id not in accessible_recorder_ids:
                        accessible_recorder_ids.append(member.oauth_user_id)
                        logger.info(f"Added department member {member.name} ({member.oauth_user_id}) to accessible list")
        else:
            # 如果找不到用户档案，只允许查看自己的记录
            # 这里需要根据实际情况调整，可能需要从其他表获取用户信息
            logger.warning(f"No user profile found for user_id: {current_user_id}")
        
        # 去重
        accessible_recorder_ids = list(set(accessible_recorder_ids))
        logger.info(f"User {current_user_id} can access {len(accessible_recorder_ids)} recorders: {accessible_recorder_ids}")
        
        return accessible_recorder_ids

    def query_visit_records(
        self,
        session: Session,
        request: VisitRecordQueryRequest,
        current_user_id: Optional[UUID] = None,
        max_page_size: int = 100,
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
        elif request.page_size > max_page_size:  # 限制最大页面大小（可配置，默认100）
            request.page_size = max_page_size
        
        # 先处理权限控制，获取可访问的recorder_ids
        uuid_recorder_ids = None
        is_admin = False
        if current_user_id:
            # 检查是否为管理团队成员
            is_admin = self._is_admin_user(current_user_id, session)
            if is_admin:
                logger.info(f"Admin user {current_user_id} detected, skipping access control")
            else:
                accessible_recorder_ids = self._get_user_accessible_recorder_ids(session, current_user_id)
                if accessible_recorder_ids:
                    # 将字符串ID转换为UUID进行过滤
                    from uuid import UUID
                    try:
                        uuid_recorder_ids = []
                        for rid in accessible_recorder_ids:
                            if rid:
                                # 如果已经是32位字符串（去掉短横线的UUID），需要重新格式化
                                if len(rid) == 32:
                                    # 重新插入短横线以创建标准UUID格式
                                    formatted_uuid = f"{rid[:8]}-{rid[8:12]}-{rid[12:16]}-{rid[16:20]}-{rid[20:32]}"
                                    uuid_recorder_ids.append(UUID(formatted_uuid))
                                else:
                                    # 标准UUID格式
                                    uuid_recorder_ids.append(UUID(rid))
                    except ValueError as e:
                        logger.error(f"Invalid UUID format in accessible_recorder_ids: {e}")
                        # 如果转换失败，返回空结果
                        return Page(
                            items=[],
                            total=0,
                            page=request.page,
                            size=request.page_size,
                            pages=0
                        )
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
            
        # 构建基础查询，关联客户表获取客户分类
        # 优化：先不JOIN UserProfile，避免使用函数导致索引失效
        # 部门信息通过子查询或后续查询获取
        query = (
            select(
                CRMSalesVisitRecord,
                CRMAccount.customer_level
            )
            .outerjoin(
                CRMAccount,
                CRMSalesVisitRecord.account_id == CRMAccount.unique_id
            )
        )
        
        # 应用权限控制过滤 - 在JOIN之前先过滤，提高性能
        if not is_admin and uuid_recorder_ids:
            query = query.where(CRMSalesVisitRecord.recorder_id.in_(uuid_recorder_ids))

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
            # 客户名称完全匹配，支持多选
            # 性能优化：使用精确匹配（可以使用索引），比模糊匹配性能更好
            query = query.where(
                CRMSalesVisitRecord.account_name.in_(request.account_name)
            )

        if request.partner_id:
            query = query.where(
                CRMSalesVisitRecord.partner_id.in_(request.partner_id)
            )
        if request.partner_name:
            # 合作伙伴名称完全匹配，支持多选
            # 性能优化：使用精确匹配（可以使用索引），比模糊匹配性能更好
            query = query.where(
                CRMSalesVisitRecord.partner_name.in_(request.partner_name)
            )

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

        # 使用拜访人的department字段作为所在团队
        # 优化：先查询符合条件的recorder_ids，然后过滤
        if request.department:
            # 先查询符合条件的UserProfile，获取对应的oauth_user_id列表
            department_user_profiles = session.exec(
                select(UserProfile.oauth_user_id).where(
                    UserProfile.department.in_(request.department)
                )
            ).all()
            
            if department_user_profiles:
                # 将oauth_user_id转换为UUID格式的recorder_id
                department_recorder_ids = []
                for oauth_id in department_user_profiles:
                    if oauth_id:
                        try:
                            from uuid import UUID
                            # 处理可能没有短横线的UUID
                            if len(oauth_id) == 32:
                                formatted_uuid = f"{oauth_id[:8]}-{oauth_id[8:12]}-{oauth_id[12:16]}-{oauth_id[16:20]}-{oauth_id[20:32]}"
                                department_recorder_ids.append(UUID(formatted_uuid))
                            else:
                                department_recorder_ids.append(UUID(oauth_id))
                        except ValueError:
                            continue
                
                if department_recorder_ids:
                    # 如果已经有权限过滤，需要取交集
                    if not is_admin and uuid_recorder_ids:
                        # 取交集
                        department_recorder_ids = [rid for rid in department_recorder_ids if rid in uuid_recorder_ids]
                    
                    if department_recorder_ids:
                        query = query.where(CRMSalesVisitRecord.recorder_id.in_(department_recorder_ids))
                    else:
                        # 没有匹配的记录，返回空结果
                        return Page(
                            items=[],
                            total=0,
                            page=request.page,
                            size=request.page_size,
                            pages=0
                        )
                else:
                    # 没有有效的recorder_id，返回空结果
                    return Page(
                        items=[],
                        total=0,
                        page=request.page,
                        size=request.page_size,
                        pages=0
                    )
            else:
                # 没有匹配的部门用户，返回空结果
                return Page(
                    items=[],
                    total=0,
                    page=request.page,
                    size=request.page_size,
                    pages=0
                )

        if request.visit_communication_method:
            query = query.where(
                CRMSalesVisitRecord.visit_communication_method.in_(request.visit_communication_method)
            )

        if request.visit_purpose:
            query = query.where(
                CRMSalesVisitRecord.visit_purpose.in_(request.visit_purpose)
            )

        if request.followup_quality_level:
            query = query.where(
                or_(
                    CRMSalesVisitRecord.followup_quality_level_zh.in_(request.followup_quality_level),
                    CRMSalesVisitRecord.followup_quality_level_en.in_(request.followup_quality_level)
                )
            )

        if request.next_steps_quality_level:
            query = query.where(
                or_(
                    CRMSalesVisitRecord.next_steps_quality_level_zh.in_(request.next_steps_quality_level),
                    CRMSalesVisitRecord.next_steps_quality_level_en.in_(request.next_steps_quality_level)
                )
            )

        if request.visit_type:
            query = query.where(
                CRMSalesVisitRecord.visit_type.in_(request.visit_type)
            )

        if request.subject:
            query = query.where(
                CRMSalesVisitRecord.subject.in_(request.subject)
            )

        if request.record_type:
            query = query.where(
                CRMSalesVisitRecord.record_type.in_(request.record_type)
            )

        if request.is_first_visit is not None:
            query = query.where(
                CRMSalesVisitRecord.is_first_visit == request.is_first_visit
            )

        if request.is_call_high is not None:
            query = query.where(
                CRMSalesVisitRecord.is_call_high == request.is_call_high
            )

        # 处理创建时间筛选 - 将北京时间的日期转换为UTC时间范围
        if request.last_modified_time_start:
            utc_start_datetime = _convert_beijing_date_to_utc_range(request.last_modified_time_start, is_start=True)
            if utc_start_datetime:
                query = query.where(CRMSalesVisitRecord.last_modified_time >= utc_start_datetime)

        if request.last_modified_time_end:
            utc_end_datetime = _convert_beijing_date_to_utc_range(request.last_modified_time_end, is_start=False)
            if utc_end_datetime:
                query = query.where(CRMSalesVisitRecord.last_modified_time <= utc_end_datetime)

        # 应用排序 - 默认按拜访日期降序
        sort_field = getattr(CRMSalesVisitRecord, request.sort_by, CRMSalesVisitRecord.visit_communication_date)
        if request.sort_direction.lower() == "desc":
            query = query.order_by(desc(sort_field))
        else:
            query = query.order_by(asc(sort_field))

        # 执行分页查询
        params = Params(page=request.page, size=request.page_size)
        result = paginate(session, query, params)

        # 优化：批量获取部门信息，避免N+1查询
        # 收集所有需要查询的recorder_id（UUID格式）
        recorder_ids = set()
        for record, customer_level in result.items:
            if record.recorder_id:
                recorder_ids.add(record.recorder_id)
        
        # 批量查询部门信息 - 一次性查询所有匹配的UserProfile
        # oauth_user_id 是带短横线的标准 UUID 字符串格式，可以直接与 recorder_id 转换后的字符串匹配
        department_map = {}
        if recorder_ids:
            # 将 UUID 转换为字符串列表（带短横线），直接使用 IN 查询
            # 这样可以使用索引，性能更好
            recorder_id_strs = [str(rid) for rid in recorder_ids]
            
            # 批量查询所有匹配的UserProfile
            user_profiles = session.exec(
                select(UserProfile.oauth_user_id, UserProfile.department).where(
                    UserProfile.oauth_user_id.in_(recorder_id_strs)
                )
            ).all()
            
            # 构建映射：将 oauth_user_id 字符串转换回 UUID 作为 key
            for oauth_user_id, department in user_profiles:
                if oauth_user_id:
                    try:
                        from uuid import UUID
                        # oauth_user_id 已经是标准 UUID 字符串格式，直接转换
                        recorder_uuid = UUID(oauth_user_id)
                        if recorder_uuid in recorder_ids:
                            department_map[recorder_uuid] = department
                    except ValueError:
                        # 如果转换失败，跳过
                        continue

        # 转换结果格式 - 复用现有模型
        items = []
        for record, customer_level in result.items:
            department = department_map.get(record.recorder_id) if record.recorder_id else None
            items.append(_convert_to_response(record, customer_level, department))

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
                UserProfile.department
            )
            .outerjoin(
                CRMAccount,
                CRMSalesVisitRecord.account_id == CRMAccount.unique_id
            )
            .outerjoin(
                UserProfile,
                func.cast(CRMSalesVisitRecord.recorder_id, String) == UserProfile.oauth_user_id
            )
            .where(CRMSalesVisitRecord.id == record_id)
        )

        # 应用权限控制
        if current_user_id:
            # 检查是否为管理团队成员
            if self._is_admin_user(current_user_id, session):
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
            record, customer_level, department = result
            return _convert_to_response(record, customer_level, department)
        
        return None


# 创建repository实例
visit_record_repo = VisitRecordRepo()
