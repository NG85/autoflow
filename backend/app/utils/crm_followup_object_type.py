"""跟进对象类型展示名（字段映射）。"""

from typing import Dict, Optional

from app.utils.crm_followup_object import FollowupObject, resolve_followup_object


def resolve_customer_attribute_display_label(
    account_id: Optional[str],
    partner_id: Optional[str],
    field_mapping: Dict[str, str],
    *,
    followup_object_type: Optional[str] = None,
    followup_object_id: Optional[str] = None,
    followup_object_name: Optional[str] = None,
) -> Optional[str]:
    """根据跟进对象类型返回字段映射中的展示名；lead 需 DB 配置 lead_title。"""
    obj = resolve_followup_object(
        followup_object_type=followup_object_type,
        followup_object_id=followup_object_id,
        followup_object_name=followup_object_name,
        account_id=account_id,
        partner_id=partner_id,
    )
    if not obj:
        return None
    label = (field_mapping.get(obj.object_type) or "").strip()
    return label or None


def resolve_customer_attribute_display_label_for_object(
    followup_object: Optional[FollowupObject],
    field_mapping: Dict[str, str],
) -> Optional[str]:
    if not followup_object:
        return None
    label = (field_mapping.get(followup_object.object_type) or "").strip()
    return label or None
