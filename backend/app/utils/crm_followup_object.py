"""跟进对象统一模型：新数据写 followup_object_*，历史 account/partner 槽位只读兼容。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

FOLLOWUP_OBJECT_TYPE_END_CUSTOMER = "end_customer"
FOLLOWUP_OBJECT_TYPE_PARTNER = "partner"
FOLLOWUP_OBJECT_TYPE_LEAD = "lead"

FOLLOWUP_OBJECT_TYPES: frozenset[str] = frozenset({
    FOLLOWUP_OBJECT_TYPE_END_CUSTOMER,
    FOLLOWUP_OBJECT_TYPE_PARTNER,
    FOLLOWUP_OBJECT_TYPE_LEAD,
})


@dataclass(frozen=True)
class FollowupObject:
    object_type: str
    object_id: str
    object_name: Optional[str] = None


def _strip(value: Optional[str]) -> str:
    return (value or "").strip()


def resolve_followup_object(
    *,
    followup_object_type: Optional[str] = None,
    followup_object_id: Optional[str] = None,
    followup_object_name: Optional[str] = None,
    account_id: Optional[str] = None,
    account_name: Optional[str] = None,
    partner_id: Optional[str] = None,
    partner_name: Optional[str] = None,
) -> Optional[FollowupObject]:
    """
    解析生效的跟进对象。
    - 优先 followup_object_*（线索及未来新类型）
    - 否则回退 account_id / partner_id（历史数据）
    """
    obj_type = _strip(followup_object_type)
    obj_id = _strip(followup_object_id)
    if obj_type and obj_id:
        obj_name = _strip(followup_object_name) or None
        return FollowupObject(object_type=obj_type, object_id=obj_id, object_name=obj_name)

    aid = _strip(account_id)
    if aid:
        return FollowupObject(
            object_type=FOLLOWUP_OBJECT_TYPE_END_CUSTOMER,
            object_id=aid,
            object_name=_strip(account_name) or None,
        )

    pid = _strip(partner_id)
    if pid:
        return FollowupObject(
            object_type=FOLLOWUP_OBJECT_TYPE_PARTNER,
            object_id=pid,
            object_name=_strip(partner_name) or None,
        )

    return None


def resolve_followup_object_from_record(record: Any) -> Optional[FollowupObject]:
    """从 ORM 行或 dict 解析跟进对象。"""
    if isinstance(record, dict):
        getter = record.get
    else:
        getter = lambda key, default=None: getattr(record, key, default)

    return resolve_followup_object(
        followup_object_type=getter("followup_object_type"),
        followup_object_id=getter("followup_object_id"),
        followup_object_name=getter("followup_object_name"),
        account_id=getter("account_id"),
        account_name=getter("account_name"),
        partner_id=getter("partner_id"),
        partner_name=getter("partner_name"),
    )


def resolve_crm_account_join_id(
    *,
    followup_object_type: Optional[str] = None,
    followup_object_id: Optional[str] = None,
    account_id: Optional[str] = None,
    partner_id: Optional[str] = None,
) -> Optional[str]:
    """用于 crm_accounts 关联：仅 end_customer / partner，不含线索。"""
    obj = resolve_followup_object(
        followup_object_type=followup_object_type,
        followup_object_id=followup_object_id,
        account_id=account_id,
        partner_id=partner_id,
    )
    if not obj or obj.object_type == FOLLOWUP_OBJECT_TYPE_LEAD:
        return None
    return obj.object_id


def apply_followup_object_to_response_dict(record_dict: Dict[str, Any]) -> None:
    """将解析后的 followup_object_* 写入响应 dict（覆盖/补齐 API 字段）。"""
    obj = resolve_followup_object_from_record(record_dict)
    if not obj:
        return
    record_dict["followup_object_type"] = obj.object_type
    record_dict["followup_object_id"] = obj.object_id
    record_dict["followup_object_name"] = obj.object_name
