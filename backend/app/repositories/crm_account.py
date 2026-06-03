from typing import List, Optional, Tuple

from sqlalchemy import func, or_, text
from sqlmodel import Session, select

from app.models.crm_accounts import CRMAccount
from app.repositories.base_repo import BaseRepo
from app.utils.crm_account_tags import (
    AccountTagOption,
    merge_distinct_account_tags,
    parse_account_tags,
)


_CRM_ACCOUNT_FILTER_OPTION_SEP = "\x1e"


def _split_group_concat(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part for part in value.split(_CRM_ACCOUNT_FILTER_OPTION_SEP) if part]


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

    def list_distinct_tags_by_account_ids(
        self,
        db_session: Session,
        account_ids: List[str],
    ) -> List[AccountTagOption]:
        if not account_ids:
            return []
        accounts = self.get_by_account_ids(db_session, account_ids)
        tags: list[AccountTagOption] = []
        for account in accounts:
            tags.extend(parse_account_tags(account.extra))
        return merge_distinct_account_tags(tags)

    def get_distinct_customer_level_and_attribute(
        self,
        db_session: Session,
    ) -> Tuple[List[str], List[str]]:
        """一次查询 crm_accounts，聚合 customer_level / customer_attribute 去重值。"""
        db_session.exec(text("SET SESSION group_concat_max_len = 1048576"))
        row = db_session.exec(
            text(
                """
                SELECT
                  GROUP_CONCAT(
                    DISTINCT customer_level ORDER BY customer_level SEPARATOR :sep
                  ) AS customer_levels,
                  GROUP_CONCAT(
                    DISTINCT customer_attribute ORDER BY customer_attribute SEPARATOR :sep
                  ) AS customer_attributes
                FROM crm_accounts
                WHERE (delete_flag = 0 OR delete_flag IS NULL)
                """
            ),
            params={"sep": _CRM_ACCOUNT_FILTER_OPTION_SEP},
        ).one()
        if row is None:
            return [], []
        return _split_group_concat(row[0]), _split_group_concat(row[1])

    def list_all_distinct_tags(self, db_session: Session) -> List[AccountTagOption]:
        not_deleted = or_(CRMAccount.delete_flag == 0, CRMAccount.delete_flag.is_(None))
        has_tags_array = func.json_type(func.json_extract(CRMAccount.extra, "$.tags")) == "ARRAY"
        rows = db_session.exec(
            select(CRMAccount.extra)
            .where(not_deleted)
            .where(CRMAccount.extra.is_not(None))
            .where(has_tags_array)
        ).all()
        tags: list[AccountTagOption] = []
        for extra in rows:
            tags.extend(parse_account_tags(extra))
        return merge_distinct_account_tags(tags)

    def get_account_unique_ids_by_tag_ids(
        self,
        db_session: Session,
        tag_ids: List[str],
        *,
        account_ids: Optional[List[str]] = None,
    ) -> List[str]:
        normalized = list({tag_id.strip() for tag_id in tag_ids if tag_id and tag_id.strip()})
        if not normalized:
            return []

        not_deleted = or_(CRMAccount.delete_flag == 0, CRMAccount.delete_flag.is_(None))
        has_tags_array = func.json_type(func.json_extract(CRMAccount.extra, "$.tags")) == "ARRAY"
        tag_match = or_(
            *[
                func.json_search(CRMAccount.extra, "one", tag_id, None, "$.tags[*].id").isnot(None)
                for tag_id in normalized
            ]
        )

        query = (
            select(CRMAccount.unique_id)
            .where(not_deleted)
            .where(CRMAccount.extra.is_not(None))
            .where(has_tags_array)
            .where(tag_match)
        )
        if account_ids:
            scoped_ids = list({account_id.strip() for account_id in account_ids if account_id and account_id.strip()})
            if not scoped_ids:
                return []
            query = query.where(CRMAccount.unique_id.in_(scoped_ids))

        rows = db_session.exec(query.distinct()).all()
        return [str(row) for row in rows if row]


crm_account_repo = CRMAccountRepo()
