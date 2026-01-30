import logging
from typing import Optional, List, Tuple
from uuid import UUID
from sqlmodel import Session, select, and_, or_, func
from sqlalchemy.exc import IntegrityError
from app.repositories.base_repo import BaseRepo
from app.models.local_contacts import LocalContact
from app.models.crm_accounts import CRMAccount
from app.models.crm_data_authority import CrmDataAuthority
from app.models.user_reporting_relation import UserReportingRelation
from app.rag.types import CrmDataType
from app.repositories.user_profile import user_profile_repo
from app.repositories.visit_record import visit_record_repo

logger = logging.getLogger(__name__)


class LocalContactRepo(BaseRepo):
    model_cls = LocalContact
    
    def _not_deleted_condition(self):
        """软删除条件：delete_flag 为 0 或 NULL"""
        return (LocalContact.delete_flag == 0) | (LocalContact.delete_flag.is_(None))
    
    def check_account_permission(
        self, 
        db_session: Session, 
        user_id: UUID, 
        customer_id: str
        ) -> bool:
        """
        检查用户是否有权限访问指定的客户
        
        Args:
            db_session: 数据库会话
            user_id: 用户ID
            customer_id: 客户ID (crm_accounts.unique_id)
            
        Returns:
            True 如果有权限，False 否则
        """
        # 1. 检查用户是否有权限访问所有CRM数据
        if visit_record_repo.can_access_all_crm_data(user_id, db_session):
            return True
        
        # 2. 获取用户的CRM用户ID
        crm_user_id = user_profile_repo.get_crm_user_id_by_user_id(db_session, user_id)
        if not crm_user_id:
            # 如果没有CRM用户ID，则没有权限
            logger.info(f"User {user_id} has no crm_user_id; no authorized items.")
            return False
        
        # 2.2 直接查询 crm_data_authority 表，检查是否有权限访问该客户
        stmt = select(CrmDataAuthority).where(
            CrmDataAuthority.crm_id == str(crm_user_id),
            CrmDataAuthority.type == CrmDataType.ACCOUNT.value,
            CrmDataAuthority.data_id == customer_id,
            (CrmDataAuthority.delete_flag.is_(None)) | (CrmDataAuthority.delete_flag == False)  # noqa: E712
        ).limit(1)
        
        result = db_session.exec(stmt).first()
        logger.info(f"CRM data authority result for user {user_id} and customer {customer_id}: {result}")
        if result is not None:
            return True

        # 2.3 兜底逻辑：
        # - 如果权限表里没有记录，先检查 crm_accounts 的负责人是否为本人
        # - 如果负责人是本人的下级（汇报关系表 user_reporting_relation，且 is_active=True），也允许访问
        # customer_id 对应 crm_accounts.unique_id
        owner_id_stmt = (
            select(CRMAccount.person_in_charge_id)
            .where(CRMAccount.unique_id == customer_id)
            .limit(1)
        )
        owner_id = db_session.exec(owner_id_stmt).first()
        logger.info(
            f"CRM account owner_id for user {user_id} (crm_user_id={crm_user_id}) "
            f"and customer {customer_id}: {owner_id}"
        )
        if not owner_id:
            return False

        # 负责人为本人
        if str(owner_id) == str(crm_user_id):
            return True

        # 负责人为下级（直接或间接均可，依赖 user_reporting_relation 的 level）
        try:
            subordinate_stmt = (
                select(UserReportingRelation.id)
                .where(UserReportingRelation.from_user_id == str(crm_user_id))
                .where(UserReportingRelation.to_user_id == str(owner_id))
                .where(UserReportingRelation.is_active == True)  # noqa: E712
                .limit(1)
            )
            subordinate_match = db_session.exec(subordinate_stmt).first()
            logger.info(
                f"CRM account subordinate owner match for user {user_id} (crm_user_id={crm_user_id}) "
                f"and customer {customer_id} (owner_id={owner_id}): {subordinate_match}"
            )
            return subordinate_match is not None
        except Exception as e:
            # nice-to-have: 汇报关系表异常不影响主逻辑（按“未命中下级”处理）
            logger.warning(
                f"Failed to check subordinate relation for user {user_id} (crm_user_id={crm_user_id}) "
                f"and customer {customer_id} (owner_id={owner_id}): {e}"
            )
            return False
    
    def get_by_id(
        self, 
        db_session: Session, 
        contact_id: str, 
        user_id: Optional[UUID] = None
    ) -> Optional[LocalContact]:
        """根据唯一ID获取联系人（带权限检查）"""
        query = select(LocalContact).where(
            LocalContact.unique_id == contact_id,
            self._not_deleted_condition()
        )
        contact = db_session.exec(query).first()
        
        if not contact:
            return None
        
        # 如果提供了用户ID，检查权限
        if user_id and not self.check_account_permission(db_session, user_id, contact.customer_id):
            return None
        
        return contact
    
    def get_by_customer_id(
        self,
        db_session: Session,
        customer_id: str,
        user_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[LocalContact], int]:
        """
        根据客户ID获取联系人列表（带权限检查）
        
        Returns:
            tuple: (联系人列表, 总数)
        """
        # 检查权限
        if user_id and not self.check_account_permission(db_session, user_id, customer_id):
            return [], 0
        
        # 构建查询条件
        conditions = [
            LocalContact.customer_id == customer_id,
            self._not_deleted_condition()
        ]
        
        # 构建 count 查询
        count_query = select(func.count(LocalContact.id)).where(and_(*conditions))
        total = db_session.exec(count_query).one()
        
        # 构建数据查询
        query = select(LocalContact).where(
            and_(*conditions)
        ).order_by(LocalContact.created_at.desc()).offset(skip).limit(limit)
        
        contacts = db_session.exec(query).all()
        return contacts, total
    
    def search(
        self,
        db_session: Session,
        user_id: UUID,
        customer_id: Optional[str] = None,
        name: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[LocalContact], int]:
        """
        搜索联系人（带权限过滤）
        
        Args:
            db_session: 数据库会话
            user_id: 用户ID
            customer_id: 可选的客户ID过滤
            name: 可选的姓名搜索（模糊匹配）
            skip: 跳过数量
            limit: 返回数量限制
            
        Returns:
            tuple: (联系人列表, 总数)（只返回用户有权限访问的客户下的联系人）
        """
        # 构建查询条件
        conditions = [self._not_deleted_condition()]
        
        # 检查是否有权限访问所有CRM数据（如果是，不需要过滤权限）
        can_access_all_crm = visit_record_repo.can_access_all_crm_data(user_id, db_session)
        authorized_account_ids = None  # 初始化变量
        
        # 如果没有权限访问所有CRM数据，需要过滤权限
        if not can_access_all_crm:
            # 获取用户的CRM用户ID
            crm_user_id = user_profile_repo.get_crm_user_id_by_user_id(db_session, user_id)
            if not crm_user_id:
                # 如果没有CRM用户ID，则没有权限
                logger.info(f"User {user_id} has no crm_user_id; no authorized items.")
                return [], 0
            
            # 查询 crm_data_authority 表获取有权限的客户ID列表
            stmt = select(CrmDataAuthority.data_id).where(
                CrmDataAuthority.crm_id == str(crm_user_id),
                CrmDataAuthority.type == CrmDataType.ACCOUNT.value,
                (CrmDataAuthority.delete_flag.is_(None)) | (CrmDataAuthority.delete_flag == False)  # noqa: E712
            )
            authorized_account_ids = [row[0] for row in db_session.exec(stmt).all()]
            
            if not authorized_account_ids:
                # 用户没有任何客户权限
                return [], 0
            
            conditions.append(LocalContact.customer_id.in_(authorized_account_ids))
        
        # 客户ID过滤（如果提供了 customer_id，需要检查权限）
        if customer_id:
            # 如果用户没有权限访问所有CRM数据，需要验证该客户是否在授权列表中
            if not can_access_all_crm and authorized_account_ids is not None:
                if customer_id not in authorized_account_ids:
                    # 用户没有权限访问该客户，返回空结果
                    return [], 0
            conditions.append(LocalContact.customer_id == customer_id)
        
        # 姓名搜索（模糊匹配）
        if name:
            conditions.append(LocalContact.name.like(f"%{name}%"))
        
        # 构建 count 查询
        count_query = select(func.count(LocalContact.id)).where(and_(*conditions))
        total = db_session.exec(count_query).one()
        
        # 构建数据查询
        query = select(LocalContact).where(
            and_(*conditions)
        ).order_by(LocalContact.created_at.desc()).offset(skip).limit(limit)
        
        contacts = db_session.exec(query).all()
        return contacts, total
    
    def create(
        self,
        db_session: Session,
        contact_data: dict,
        user_id: UUID
    ) -> LocalContact:
        """
        创建联系人
        如果已存在相同的联系人：
        - 如果已删除（delete_flag=True），则恢复并更新
        - 如果未删除，则返回已存在的联系人
        
        Args:
            db_session: 数据库会话
            contact_data: 联系人数据字典
            user_id: 创建人ID
            
        Returns:
            创建或已存在的联系人对象
            
        Raises:
            ValueError: 如果用户没有权限访问指定的客户
        """
        customer_id = contact_data.get("customer_id")
        name = contact_data.get("name")
        position = contact_data.get("position")
        
        if not customer_id:
            raise ValueError("customer_id is required")
        if not name:
            raise ValueError("name is required")
        if not position:
            raise ValueError("position is required")
        
        # 检查权限
        if not self.check_account_permission(db_session, user_id, customer_id):
            raise ValueError(f"User does not have permission to create contact for customer {customer_id}")
        
        # 验证客户是否存在
        account = db_session.exec(
            select(CRMAccount).where(CRMAccount.unique_id == customer_id)
        ).first()
        if not account:
            raise ValueError(f"Customer with id {customer_id} not found")
        
        # 填充客户名称（如果未提供）
        if not contact_data.get("customer_name") and account.customer_name:
            contact_data["customer_name"] = account.customer_name
        
        # 去重检查：使用客户+姓名+职位
        # 排除已删除的联系人，避免与新建混淆
        # 使用数据库锁防止并发创建重复记录
        existing_contact = db_session.exec(
            select(LocalContact).where(
                and_(
                    LocalContact.customer_id == customer_id,
                    LocalContact.name == name,
                    LocalContact.position == position,
                    self._not_deleted_condition()  # 排除已删除的联系人
                )
            ).with_for_update()
        ).first()
        
        if existing_contact:
            # 如果已存在且未删除，直接返回已存在的联系人
            # 添加标记，表示是已存在的联系人（使用 __dict__ 避免 SQLModel 验证）
            object.__setattr__(existing_contact, 'is_existing', True)
            return existing_contact
        
        # 不存在相同的联系人，创建新的
        # 生成唯一ID（如果未提供）
        if not contact_data.get("unique_id"):
            from app.utils.uuid6 import uuid7
            contact_data["unique_id"] = uuid7().hex.replace("-", "")
        
        # 创建联系人对象
        from datetime import datetime
        contact = LocalContact(
            **contact_data,
            created_by=user_id,
            updated_by=user_id,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        try:
            db_session.add(contact)
            db_session.commit()
            db_session.refresh(contact)
            # 添加标记，表示是新创建的联系人（使用 __dict__ 避免 SQLModel 验证）
            object.__setattr__(contact, 'is_existing', False)
            return contact
        except IntegrityError:
            # 处理并发情况：如果创建时发生唯一性约束冲突（如 unique_id 重复）
            # 回滚后重新查询一次（可能在锁释放后，另一个请求已经创建了）
            db_session.rollback()
            
            # 重新查询：使用客户+姓名+职位，排除已删除的联系人
            existing_contact = db_session.exec(
                select(LocalContact).where(
                    and_(
                        LocalContact.customer_id == customer_id,
                        LocalContact.name == name,
                        LocalContact.position == position,
                        self._not_deleted_condition()  # 排除已删除的联系人
                    )
                )
            ).first()
            
            if existing_contact:
                # 如果找到了已存在的联系人，返回它
                # 添加标记，表示是已存在的联系人（使用 __dict__ 避免 SQLModel 验证）
                object.__setattr__(existing_contact, 'is_existing', True)
                return existing_contact
            
            # 如果还是没找到，重新抛出异常（可能是其他类型的 IntegrityError）
            raise
        except Exception as e:
            # 其他类型的错误，回滚并重新抛出
            db_session.rollback()
            raise
    
    def update(
        self,
        db_session: Session,
        contact_id: int,
        contact_data: dict,
        user_id: UUID
    ) -> Optional[LocalContact]:
        """
        更新联系人基础信息
        
        注意：
            - 只允许修改基础信息，不允许修改所属客户（customer_id）
            - 用户必须有该联系人所属客户的权限才能进行修改
            - 不允许修改唯一标识、审计字段和删除标识
        
        Args:
            db_session: 数据库会话
            contact_id: 联系人ID
            contact_data: 要更新的数据字典
            user_id: 更新人ID
            
        Returns:
            更新后的联系人对象，如果不存在或无权访问则返回None
            
        Raises:
            ValueError: 如果用户没有权限，或尝试修改不允许的字段（customer_id, customer_name, unique_id等）
        """
        contact = self.get_by_id(db_session, contact_id, user_id)
        if not contact:
            return None
        
        # 检查用户是否有权限访问该联系人所属的客户
        if not self.check_account_permission(db_session, user_id, contact.customer_id):
            raise ValueError(f"User does not have permission to modify contact for customer {contact.customer_id}")
        
        # 不允许修改所属客户相关字段
        if "customer_id" in contact_data:
            raise ValueError("Cannot modify customer_id. Contact's customer association cannot be changed.")
        if "customer_name" in contact_data:
            raise ValueError("Cannot modify customer_name. This field is automatically managed based on customer_id.")
        
        # 不允许修改唯一标识和审计字段
        forbidden_fields = ["unique_id", "created_by", "created_at", "updated_by", "updated_at", "delete_flag"]
        for field in forbidden_fields:
            if field in contact_data:
                raise ValueError(f"Cannot modify {field}. This field is protected.")
        
        # 更新字段（只允许修改基础信息）
        from datetime import datetime
        for key, value in contact_data.items():
            if hasattr(contact, key):
                setattr(contact, key, value)
        
        contact.updated_by = user_id
        contact.updated_at = datetime.now()
        
        db_session.add(contact)
        db_session.commit()
        db_session.refresh(contact)
        return contact
    
    def delete(
        self,
        db_session: Session,
        contact_id: int,
        user_id: UUID
    ) -> bool:
        """
        软删除联系人
        
        Args:
            db_session: 数据库会话
            contact_id: 联系人ID
            user_id: 删除人ID
            
        Returns:
            True 如果删除成功，False 如果联系人不存在或无权访问
        """
        contact = self.get_by_id(db_session, contact_id, user_id)
        if not contact:
            return False
        
        # 软删除
        from datetime import datetime
        contact.delete_flag = 1
        contact.updated_by = user_id
        contact.updated_at = datetime.now()
        
        db_session.add(contact)
        db_session.commit()
        return True


local_contact_repo = LocalContactRepo()
