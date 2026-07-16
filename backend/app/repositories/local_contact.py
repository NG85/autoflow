import logging
from typing import Optional, List, Tuple
from uuid import UUID
from sqlmodel import Session, select, and_, or_, func
from sqlalchemy.exc import IntegrityError
from app.repositories.base_repo import BaseRepo
from app.models.local_contacts import LocalContact
from app.models.crm_accounts import CRMAccount
from app.models.crm_leads import CRMLead
from app.permissions.crm_contact_permission_service import (
    CRM_ACCOUNT_VIEW_PERMISSION,
    crm_contact_permission_service,
)

logger = logging.getLogger(__name__)


class LocalContactRepo(BaseRepo):
    model_cls = LocalContact
    
    def _not_deleted_condition(self):
        """软删除条件：delete_flag 为 0 或 NULL"""
        return (LocalContact.delete_flag == 0) | (LocalContact.delete_flag.is_(None))

    def _get_account_by_id(self, db_session: Session, customer_id: str) -> Optional[CRMAccount]:
        return db_session.exec(
            select(CRMAccount).where(CRMAccount.unique_id == customer_id)
        ).first()

    def _get_lead_by_id(self, db_session: Session, customer_id: str) -> Optional[CRMLead]:
        return db_session.exec(
            select(CRMLead).where(CRMLead.unique_id == customer_id)
        ).first()

    def _resolve_customer_name(
        self,
        account: Optional[CRMAccount],
        lead: Optional[CRMLead],
    ) -> Optional[str]:
        if account and account.customer_name:
            return account.customer_name
        if lead:
            return lead.company_name or lead.lead_name
        return None

    def check_account_permission(
        self,
        db_session: Session,
        user_id: UUID,
        customer_id: str,
        *,
        permission: str = CRM_ACCOUNT_VIEW_PERMISSION,
    ) -> bool:
        """
        检查用户是否有权限访问指定的客户。

        走 OAuth ``POST /permission/check``（resource=crm_account）。

        Args:
            db_session: 数据库会话
            user_id: 用户ID
            customer_id: 客户ID (crm_accounts.unique_id 或 crm_leads.unique_id)
            permission: OAuth permission code（创建联系人用 ``crm:contact:create``）

        Returns:
            True 如果有权限，False 否则
        """
        return crm_contact_permission_service.check_account_access(
            db_session,
            user_id,
            customer_id,
            permission=permission,
        )    
    def get_by_id(
        self,
        db_session: Session,
        contact_id: str,
        user_id: Optional[UUID] = None,
    ) -> Optional[LocalContact]:
        """根据唯一ID获取联系人；传 user_id 时按所属客户做 OAuth 校验。"""
        query = select(LocalContact).where(
            LocalContact.unique_id == contact_id,
            self._not_deleted_condition(),
        )
        contact = db_session.exec(query).first()

        if not contact:
            return None

        if user_id and not crm_contact_permission_service.check_view(
            db_session,
            user_id,
            customer_id=contact.customer_id,
        ):
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
        根据客户ID获取联系人列表。

        权限由调用方（路由）负责；``user_id`` 仅保留兼容，不再二次鉴权。
        """
        conditions = [
            LocalContact.customer_id == customer_id,
            self._not_deleted_condition()
        ]

        count_query = select(func.count(LocalContact.id)).where(and_(*conditions))
        total = db_session.exec(count_query).one()

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

        权限：OAuth ``crm:contact:view`` 门控由路由负责；本方法用
        ``data-scope(entity=crm_account)`` 按所属客户过滤。

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
        conditions = [self._not_deleted_condition()]
        conditions.append(
            crm_contact_permission_service.list_perm_where(db_session, user_id)
        )

        if customer_id:
            conditions.append(LocalContact.customer_id == customer_id)

        if name:
            conditions.append(LocalContact.name.like(f"%{name}%"))

        count_query = select(func.count(LocalContact.id)).where(and_(*conditions))
        total = db_session.exec(count_query).one()

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

        # 权限由路由层 OAuth check 负责，此处不再二次鉴权

        # 验证客户/线索是否存在（customer_id 可对应 crm_accounts 或 crm_leads）
        account = self._get_account_by_id(db_session, customer_id)
        lead = None if account else self._get_lead_by_id(db_session, customer_id)
        if not account and not lead:
            raise ValueError(f"Customer with id {customer_id} not found")

        # 填充客户名称（如果未提供）
        if not contact_data.get("customer_name"):
            customer_name = self._resolve_customer_name(account, lead)
            if customer_name:
                contact_data["customer_name"] = customer_name
        
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
        更新联系人基础信息。

        权限由路由层 OAuth ``crm:contact:edit`` 负责；本方法只做字段约束与落库。
        """
        contact = self.get_by_id(db_session, contact_id)
        if not contact:
            return None

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
        软删除联系人。

        权限由路由层 OAuth ``crm:contact:delete`` 负责。
        """
        contact = self.get_by_id(db_session, contact_id)
        if not contact:
            return False

        from datetime import datetime
        contact.delete_flag = 1
        contact.updated_by = user_id
        contact.updated_at = datetime.now()

        db_session.add(contact)
        db_session.commit()
        return True


local_contact_repo = LocalContactRepo()
