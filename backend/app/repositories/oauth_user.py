import logging

from typing import Optional
from pydantic import deprecated
from sqlmodel import Session, select
from app.models.oauth_user import OAuthUser
from app.repositories.base_repo import BaseRepo


logger = logging.getLogger(__name__)

@deprecated("Use UserOAuthAccount instead")
class OAuthUserRepo(BaseRepo):
    model_cls = OAuthUser

    def get_by_name(self, db_session: Session, name: str) -> Optional[OAuthUser]:
        """通过姓名查找OAuth用户（精确匹配）"""
        return self._get_by_condition(db_session, name=name)

    def get_by_ask_id(self, db_session: Session, ask_id: str) -> Optional[OAuthUser]:
        """通过ask_id查找OAuth用户"""
        return self._get_by_condition(db_session, ask_id=ask_id)

    def get_by_open_id(self, db_session: Session, open_id: str) -> Optional[OAuthUser]:
        """通过open_id查找OAuth用户"""
        return self._get_by_condition(db_session, open_id=open_id)

    def _get_by_condition(self, db_session: Session, **kwargs) -> Optional[OAuthUser]:
        """通用的条件查询方法"""
        # 过滤掉空值和空字符串
        conditions = {k: v for k, v in kwargs.items() if v and str(v).strip()}
        
        if not conditions:
            return None
        
        try:
            # 构建查询条件
            where_conditions = [OAuthUser.delete_flag == 0]
            for field, value in conditions.items():
                if hasattr(OAuthUser, field):
                    where_conditions.append(getattr(OAuthUser, field) == str(value).strip())
            
            return db_session.exec(
                select(OAuthUser).where(*where_conditions)
            ).first()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in _get_by_condition with {conditions}: {e}")
            return None


# 创建repository实例
# oauth_user_repo = OAuthUserRepo()
