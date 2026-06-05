"""跟进对象类型（end_customer / partner）与字段映射展示名。"""

from typing import Dict, Optional


def resolve_customer_attribute_display_label(
    account_id: Optional[str],
    partner_id: Optional[str],
    field_mapping: Dict[str, str],
) -> Optional[str]:
    """
    根据 account_id / partner_id 谁有值判定跟进对象类型，返回映射后的展示名。
    - 有 account_id：end_customer
    - 否则有 partner_id：partner
    """
    if (account_id or "").strip():
        label = (field_mapping.get("end_customer") or "").strip()
        return label or None
    if (partner_id or "").strip():
        label = (field_mapping.get("partner") or "").strip()
        return label or None
    return None
