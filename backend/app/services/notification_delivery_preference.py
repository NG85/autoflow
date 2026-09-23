"""有资格的人默认接收；receive=false 从实发名单剔除。无行视为接收。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import UUID

from sqlmodel import Session, select

from app.models.notification_delivery_preference import NotificationDeliveryPreference
from app.services.notification_scene_catalog import (
    PREF_ELIGIBLE_OPT_OUT,
    get_scene,
    normalize_scene,
)


def _norm_variant(variant: Optional[str]) -> str:
    return str(variant or "").strip()


def _norm_department_id(department_id: Optional[str]) -> str:
    return str(department_id or "").strip()


def recipient_user_id(recipient: Dict[str, Any]) -> Optional[str]:
    raw = recipient.get("user_id") or recipient.get("userId")
    uid = str(raw or "").strip()
    return uid or None


def is_opted_out(
    session: Session,
    *,
    user_id: str,
    scene: str,
    variant: Optional[str] = None,
    department_ids: Optional[Sequence[str]] = None,
) -> bool:
    """任一条 matching receive=false 即关掉。variant 空行覆盖该 scene 全部变体。"""
    spec = get_scene(scene)
    if not spec or spec.preference != PREF_ELIGIBLE_OPT_OUT:
        return False
    try:
        uid = UUID(str(user_id))
    except (TypeError, ValueError):
        return False

    scene_key = normalize_scene(scene)
    variant_key = _norm_variant(variant)
    depts = [_norm_department_id(d) for d in (department_ids or [""])]
    if not depts:
        depts = [""]

    rows = session.exec(
        select(NotificationDeliveryPreference).where(
            NotificationDeliveryPreference.user_id == uid,
            NotificationDeliveryPreference.scene == scene_key,
        )
    ).all()
    for row in rows:
        if row.receive:
            continue
        row_variant = _norm_variant(row.variant)
        if row_variant and row_variant != variant_key:
            continue
        row_dept = _norm_department_id(row.department_id)
        if row_dept:
            if row_dept not in depts:
                continue
        elif spec.requires_department:
            # 空 department_id：部门 scene 表示关掉自己负责的全部部门
            pass
        elif "" not in depts:
            continue
        return True
    return False


def filter_opted_out_recipients(
    session: Session,
    recipients: Iterable[Dict[str, Any]],
    *,
    scene: str,
    variant: Optional[str] = None,
    department_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for recipient in recipients or []:
        uid = recipient_user_id(recipient)
        if uid and is_opted_out(
            session,
            user_id=uid,
            scene=scene,
            variant=variant,
            department_ids=department_ids,
        ):
            continue
        kept.append(recipient)
    return kept


def get_preference(
    session: Session,
    *,
    user_id: UUID,
    scene: str,
    variant: str = "",
    department_id: str = "",
) -> Optional[NotificationDeliveryPreference]:
    return session.exec(
        select(NotificationDeliveryPreference).where(
            NotificationDeliveryPreference.user_id == user_id,
            NotificationDeliveryPreference.scene == normalize_scene(scene),
            NotificationDeliveryPreference.variant == _norm_variant(variant),
            NotificationDeliveryPreference.department_id == _norm_department_id(department_id),
        )
    ).first()


def upsert_preference(
    session: Session,
    *,
    user_id: UUID,
    scene: str,
    receive: bool,
    variant: str = "",
    department_id: str = "",
) -> NotificationDeliveryPreference:
    existing = get_preference(
        session,
        user_id=user_id,
        scene=scene,
        variant=variant,
        department_id=department_id,
    )
    if existing:
        existing.receive = bool(receive)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    row = NotificationDeliveryPreference(
        user_id=user_id,
        scene=normalize_scene(scene),
        variant=_norm_variant(variant),
        department_id=_norm_department_id(department_id),
        receive=bool(receive),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_preferences_for_user(
    session: Session,
    user_id: UUID,
) -> List[NotificationDeliveryPreference]:
    return list(
        session.exec(
            select(NotificationDeliveryPreference).where(
                NotificationDeliveryPreference.user_id == user_id
            )
        ).all()
    )
