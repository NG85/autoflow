from typing import Optional, List
from sqlmodel import Session, select
from app.repositories.base_repo import BaseRepo
from app.models.crm_accounts import CRMAccount


class CRMAccountRepo(BaseRepo):
    model_cls = CRMAccount
    
    def get_by_unique_id(self, db_session: Session, unique_id: str) -> Optional[CRMAccount]:
        """根据唯一ID获取客户信息"""
        query = select(CRMAccount).where(CRMAccount.unique_id == unique_id)
        return db_session.exec(query).first()
    
    def get_by_account_ids(self, db_session: Session, account_ids: List[str]) -> List[CRMAccount]:
        """根据客户ID列表批量获取客户信息"""
        if not account_ids:
            return []
        
        query = select(CRMAccount).where(CRMAccount.unique_id.in_(account_ids))
        return db_session.exec(query).all()
    
    def get_accounts_by_person_in_charge(self, db_session: Session, person_in_charge: str) -> List[CRMAccount]:
        """根据负责人获取客户列表"""
        query = select(CRMAccount).where(CRMAccount.person_in_charge == person_in_charge)
        return db_session.exec(query).all()
    
    def get_accounts_by_department(self, db_session: Session, department: str) -> List[CRMAccount]:
        """根据部门获取客户列表"""
        query = select(CRMAccount).where(CRMAccount.department == department)
        return db_session.exec(query).all()
