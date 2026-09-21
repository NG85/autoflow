"""推送规则预览：资格名单 vs 实发（扣掉 opted_out），不发送。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.crm.save_engine import _crm_visit_record_row_to_push_dict
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.platforms.notification_types import (
    PERM_DAILY_REPORT_TEAM_RECEIVE,
    PERM_WEEKLY_REPORT_TEAM_RECEIVE,
)
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
from app.services.notification_delivery_preference import (
    is_opted_out,
    list_preferences_for_user,
    recipient_user_id,
)
from app.services.notification_scene_catalog import (
    HIGHLIGHTS_SLOTS,
    POLICY_SLOTS,
    PREF_ELIGIBLE_OPT_OUT,
    SCENE_COMPANY_DAILY,
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_DAILY,
    SCENE_DEPARTMENT_HIGHLIGHTS,
    SCENE_DEPARTMENT_WEEKLY,
    SCENE_REVIEW_SESSION,
    SCENE_SALES_DAILY,
    SCENE_VISIT_RECORD,
    VARIANT_KPI_CARD,
    VARIANT_RECAP_LITE,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_VISIT_CARD,
    VARIANT_VISIT_REPORT,
    get_scene,
    list_scenes,
    normalize_scene,
    scene_allows_variant,
)
from app.services.report_push_policy import load_report_push_policy
from app.services.visit_record_push_policy import (
    CARD_RECAP_LITE,
    load_visit_record_push_policy,
)
from app.site_settings import SiteSetting


def _reason_for_recipient_type(recipient_type: Optional[str]) -> str:
    mapping = {
        "recorder": "recorder",
        "leader": "leader",
        "configured_cc": "cc_rule",
        "collaborative_participant": "collaborative_participant",
        "department_manager": "dept_leader",
        "company_executive": "oauth_receive",
        "named_recipient": "named_recipient",
        "weekly_report_recipient": "oauth_receive",
    }
    return mapping.get(str(recipient_type or ""), str(recipient_type or "unknown"))


def catalog_payload() -> List[Dict[str, Any]]:
    policy = load_report_push_policy()
    visit_policy = load_visit_record_push_policy()
    items = []
    for spec in list_scenes():
        variants = []
        for variant in spec.variants:
            variants.append(
                {
                    "variant": variant,
                    "enabled": _variant_enabled(spec.scene, variant, policy, visit_policy),
                }
            )
        items.append(
            {
                "scene": spec.scene,
                "title": spec.title,
                "routing": spec.routing,
                "preference": spec.preference,
                "requires_department": spec.requires_department,
                "requires_record_id": spec.requires_record_id,
                "includes_groups": spec.includes_groups,
                "description": spec.description,
                "variants": variants,
            }
        )
    return items


def _variant_enabled(scene: str, variant: str, report_policy=None, visit_policy=None) -> bool:
    if scene in POLICY_SLOTS:
        report_policy = report_policy or load_report_push_policy()
        return report_policy.variant_enabled(scene, variant)
    if scene == SCENE_VISIT_RECORD:
        visit_policy = visit_policy or load_visit_record_push_policy()
        if variant == VARIANT_RECAP_LITE:
            return visit_policy.uses_recap_lite()
        return any(
            spec.enabled for spec in (*visit_policy.recipients.values(), *visit_policy.groups.values())
        )
    return True


def _person_from_recipient(
    recipient: Dict[str, Any],
    *,
    extra_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    uid = recipient_user_id(recipient)
    reasons = [_reason_for_recipient_type(recipient.get("type"))]
    if extra_reasons:
        reasons.extend(extra_reasons)
    open_id = str(recipient.get("open_id") or "").strip()
    return {
        "user_id": uid,
        "name": recipient.get("name"),
        "platform": recipient.get("platform"),
        "open_id": open_id or None,
        "has_open_id": bool(open_id),
        "type": recipient.get("type"),
        "department": recipient.get("department") or recipient.get("department_name"),
        "reasons": reasons,
    }


def _resolve_department_ids(
    db_session: Session,
    *,
    department_id: Optional[str],
    department_name: Optional[str],
) -> tuple[List[str], str]:
    dept_id = str(department_id or "").strip()
    dept_name = str(department_name or "").strip()
    ids: List[str] = []
    if dept_id:
        ids = [dept_id]
        if not dept_name:
            dept_name = department_mirror_repo.get_department_name_by_id(db_session, dept_id) or ""
    elif dept_name:
        ids = department_mirror_repo.get_department_ids_by_name(db_session, dept_name)
    return ids, dept_name


def _split_eligible_and_send(
    db_session: Session,
    recipients: List[Dict[str, Any]],
    *,
    scene: str,
    variant: str,
    department_ids: List[str],
    extra_reason: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    extra = [extra_reason] if extra_reason else None
    eligible = [_person_from_recipient(r, extra_reasons=extra) for r in recipients]
    opted_out_people: List[Dict[str, Any]] = []
    will_send: List[Dict[str, Any]] = []
    spec = get_scene(scene)
    for recipient, person in zip(recipients, eligible):
        uid = person.get("user_id")
        if (
            spec
            and spec.preference == PREF_ELIGIBLE_OPT_OUT
            and uid
            and is_opted_out(
                db_session,
                user_id=uid,
                scene=scene,
                variant=variant,
                department_ids=department_ids or [""],
            )
        ):
            opted_out_people.append(person)
            continue
        if person.get("has_open_id"):
            will_send.append(person)
    return eligible, will_send, opted_out_people


def preview_notification(
    db_session: Session,
    *,
    scene: str,
    variant: str = "",
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
    record_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    scene_key = normalize_scene(scene)
    spec = get_scene(scene_key)
    if not spec:
        return {
            "scene": scene_key,
            "variant": variant,
            "enabled": False,
            "skip_reason": "unknown_scene",
            "eligible": [],
            "will_send": [],
            "opted_out": [],
            "groups": [],
        }
    variant = str(variant or "").strip() or spec.variants[0]
    if not scene_allows_variant(scene_key, variant):
        return {
            "scene": scene_key,
            "variant": variant,
            "enabled": False,
            "skip_reason": "unknown_variant",
            "eligible": [],
            "will_send": [],
            "opted_out": [],
            "groups": [],
        }

    enabled = _variant_enabled(scene_key, variant)
    base = {
        "scene": scene_key,
        "variant": variant,
        "title": spec.title,
        "routing": spec.routing,
        "preference": spec.preference,
        "enabled": enabled,
        "skip_reason": None if enabled else "variant_disabled",
        "eligible": [],
        "will_send": [],
        "opted_out": [],
        "groups": [],
        "policy": {},
    }

    if scene_key in POLICY_SLOTS:
        policy = load_report_push_policy()
        if scene_key in HIGHLIGHTS_SLOTS:
            base["policy"] = {
                "today_highlights": policy.variant_enabled(
                    scene_key, VARIANT_TODAY_HIGHLIGHTS
                ),
                "override_user_ids": policy.override_user_ids(scene_key, variant),
            }
        else:
            policy_payload = {
                "kpi_card": policy.variant_enabled(scene_key, VARIANT_KPI_CARD),
                "visit_report": policy.variant_enabled(scene_key, VARIANT_VISIT_REPORT),
                "override_user_ids": policy.override_user_ids(scene_key, variant),
            }
            if scene_key == SCENE_SALES_DAILY:
                policy_payload["today_highlights"] = policy.variant_enabled(
                    scene_key, VARIANT_TODAY_HIGHLIGHTS
                )
            base["policy"] = policy_payload
        if scene_key == SCENE_SALES_DAILY:
            base["skip_reason"] = base["skip_reason"] or "preview_requires_no_bulk_sales_list"
            return base
        return _preview_report(
            db_session,
            spec=spec,
            scene=scene_key,
            variant=variant,
            department_id=department_id,
            department_name=department_name,
            base=base,
            service=platform_notification_service,
        )

    if scene_key == SCENE_VISIT_RECORD:
        return _preview_visit_record(
            db_session,
            record_id=record_id,
            variant=variant,
            base=base,
            service=platform_notification_service,
        )

    if scene_key == SCENE_REVIEW_SESSION:
        base["skip_reason"] = base["skip_reason"] or "preview_not_wired"
        return base

    return base


def _preview_report(
    db_session: Session,
    *,
    spec,
    scene: str,
    variant: str,
    department_id: Optional[str],
    department_name: Optional[str],
    base: Dict[str, Any],
    service,
) -> Dict[str, Any]:
    policy = load_report_push_policy()
    override_ids = policy.override_user_ids(scene, variant)
    dept_ids: List[str] = []
    dept_name = ""

    if spec.requires_department:
        dept_ids, dept_name = _resolve_department_ids(
            db_session, department_id=department_id, department_name=department_name
        )
        if not dept_name:
            base["skip_reason"] = base["skip_reason"] or "department_required"
            return base
        recipients = service.resolve_department_report_recipients(
            db_session, dept_name
        )
        perm = (
            PERM_WEEKLY_REPORT_TEAM_RECEIVE
            if scene == SCENE_DEPARTMENT_WEEKLY
            else PERM_DAILY_REPORT_TEAM_RECEIVE
        )
        if scene != SCENE_DEPARTMENT_HIGHLIGHTS:
            recipients = service._filter_recipients_by_receive_permission(
                db_session,
                recipients,
                perm,
                report_kind=f"{scene} preview",
            )
        if spec.includes_groups:
            groups = service._get_group_chats_by_department(
                department_id=dept_ids[0] if dept_ids else None,
                department_name=dept_name,
                notification_type="department_review",
                db_session=db_session,
                review_scope="report",
            )
            base["groups"] = [
                {
                    "chat_id": g.get("chat_id"),
                    "platform": g.get("platform"),
                    "name": g.get("name") or g.get("department_name"),
                    "notification_type": g.get("notification_type"),
                }
                for g in groups
            ]
        extra_reason = None
    elif scene == SCENE_COMPANY_DAILY:
        recipients = service.get_recipients_for_company_daily_report(db_session)
        extra_reason = None
    elif scene == SCENE_COMPANY_HIGHLIGHTS:
        recipients = service.get_recipients_for_company_highlights(db_session)
        extra_reason = "named_recipient"
    else:
        recipients = service.get_recipients_for_company_weekly_report(db_session)
        extra_reason = None

    if override_ids and scene != SCENE_COMPANY_HIGHLIGHTS:
        recipients = service.recipients_from_user_ids(
            db_session, override_ids, recipient_type="variant_override"
        )
        extra_reason = "variant_override"

    eligible, will_send, opted_out = _split_eligible_and_send(
        db_session,
        recipients,
        scene=scene,
        variant=variant,
        department_ids=dept_ids or [""],
        extra_reason=extra_reason,
    )
    base["eligible"] = eligible
    if not base["enabled"]:
        base["will_send"] = []
        base["opted_out"] = opted_out
        return base
    base["will_send"] = will_send
    base["opted_out"] = opted_out
    return base


def _preview_visit_record(
    db_session: Session,
    *,
    record_id: Optional[str],
    variant: str,
    base: Dict[str, Any],
    service,
) -> Dict[str, Any]:
    rid = str(record_id or "").strip()
    if not rid:
        base["skip_reason"] = base["skip_reason"] or "record_id_required"
        return base
    row = db_session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == rid)
    ).first()
    if not row:
        base["skip_reason"] = "visit_record_not_found"
        base["enabled"] = False
        return base

    visit_record = _crm_visit_record_row_to_push_dict(row)
    recipients_by_platform, review_groups, brief_groups = (
        service._collect_visit_record_recipients_and_groups(
            db_session,
            recorder_name=visit_record.get("recorder"),
            recorder_id=str(row.recorder_id) if row.recorder_id else None,
            visit_record=visit_record,
        )
    )
    policy = load_visit_record_push_policy()
    base["policy"] = {
        "recipients": {
            role: {"enabled": spec.enabled, "card": spec.card}
            for role, spec in policy.recipients.items()
        },
        "groups": {
            role: {"enabled": spec.enabled, "card": spec.card}
            for role, spec in policy.groups.items()
        },
    }
    people: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for _platform, recips in recipients_by_platform.items():
        for recipient in recips:
            rtype = recipient.get("type")
            if not policy.recipient_enabled(rtype):
                continue
            card = policy.recipient_card(rtype)
            if variant == VARIANT_RECAP_LITE and card != CARD_RECAP_LITE:
                continue
            if variant == VARIANT_VISIT_CARD and card == CARD_RECAP_LITE:
                continue
            key = f"{recipient.get('platform')}:{recipient.get('open_id')}:{rtype}"
            if key in seen:
                continue
            seen.add(key)
            people.append(_person_from_recipient(recipient, extra_reasons=[f"card:{card}"]))

    groups_out = []
    if policy.group_enabled("review"):
        for g in review_groups:
            groups_out.append(
                {
                    "chat_id": g.get("chat_id"),
                    "platform": g.get("platform"),
                    "role": "review",
                    "notification_type": g.get("notification_type"),
                }
            )
    if policy.group_enabled("brief"):
        for g in brief_groups:
            groups_out.append(
                {
                    "chat_id": g.get("chat_id"),
                    "platform": g.get("platform"),
                    "role": "brief",
                    "notification_type": g.get("notification_type"),
                }
            )
    base["eligible"] = people
    base["will_send"] = [p for p in people if p.get("has_open_id")] if base["enabled"] else []
    base["groups"] = groups_out
    base["record_id"] = rid
    return base


def _preference_receive(
    prefs: Dict[tuple, bool],
    *,
    scene: str,
    variant: str,
    department_id: str,
) -> bool:
    if (scene, variant, department_id) in prefs:
        return prefs[(scene, variant, department_id)]
    if (scene, "", department_id) in prefs:
        return prefs[(scene, "", department_id)]
    return True


def user_is_eligible(
    db_session: Session,
    *,
    user_id: str,
    scene: str,
    variant: str = "",
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
) -> bool:
    spec = get_scene(scene)
    if not spec or spec.preference != PREF_ELIGIBLE_OPT_OUT:
        return False
    variants = [variant] if str(variant or "").strip() else list(spec.variants)
    uid = str(user_id)
    dept_id = str(department_id or "").strip()
    dept_name = str(department_name or "").strip()

    if spec.scene == SCENE_SALES_DAILY:
        from app.platforms.notification_types import PERM_DAILY_REPORT_PERSONAL_RECEIVE
        from app.services.platform_notification_service import platform_notification_service

        try:
            uid_uuid = UUID(uid)
        except (TypeError, ValueError):
            return False
        return platform_notification_service._user_has_receive_permission(
            uid_uuid, PERM_DAILY_REPORT_PERSONAL_RECEIVE
        )

    if spec.requires_department and not dept_id and not dept_name:
        policy = load_report_push_policy()
        for item in variants:
            if uid in set(policy.override_user_ids(spec.scene, item)):
                return True
        for leader_dept_id, _placeholder in user_department_relation_repo.list_leader_departments_for_user(
            db_session, uid
        ):
            if user_is_eligible(
                db_session,
                user_id=uid,
                scene=spec.scene,
                variant=variant,
                department_id=leader_dept_id,
            ):
                return True
        return False

    for item in variants:
        preview = preview_notification(
            db_session,
            scene=spec.scene,
            variant=item,
            department_id=dept_id or None,
            department_name=dept_name or None,
        )
        if any(str(p.get("user_id") or "") == uid for p in preview.get("eligible") or []):
            return True
    return False


def list_eligible_preference_targets(
    db_session: Session,
    user_id: UUID,
) -> List[Dict[str, Any]]:
    """列出当前用户有资格且可关掉的 scene × variant（部门仅自己负责的）。"""
    uid = str(user_id)
    prefs = {
        (row.scene, row.variant or "", row.department_id or ""): bool(row.receive)
        for row in list_preferences_for_user(db_session, user_id)
    }
    items: List[Dict[str, Any]] = []

    sales_spec = get_scene(SCENE_SALES_DAILY)
    if sales_spec and user_is_eligible(
        db_session, user_id=uid, scene=SCENE_SALES_DAILY
    ):
        for variant in sales_spec.variants:
            receive = _preference_receive(
                prefs, scene=SCENE_SALES_DAILY, variant=variant, department_id=""
            )
            items.append(
                {
                    "scene": SCENE_SALES_DAILY,
                    "variant": variant,
                    "department_id": "",
                    "department_name": "",
                    "title": sales_spec.title,
                    "receive": receive,
                    "opted_out": not receive,
                }
            )

    for scene in (SCENE_COMPANY_DAILY, SCENE_COMPANY_WEEKLY, SCENE_COMPANY_HIGHLIGHTS):
        spec = get_scene(scene)
        if not spec:
            continue
        for variant in spec.variants:
            preview = preview_notification(db_session, scene=scene, variant=variant)
            if not any(str(p.get("user_id") or "") == uid for p in preview.get("eligible") or []):
                continue
            receive = _preference_receive(prefs, scene=scene, variant=variant, department_id="")
            items.append(
                {
                    "scene": scene,
                    "variant": variant,
                    "department_id": "",
                    "department_name": "",
                    "title": spec.title,
                    "receive": receive,
                    "opted_out": not receive,
                }
            )

    leader_depts = user_department_relation_repo.list_leader_departments_for_user(
        db_session, uid
    )
    seen_dept_ids: set[str] = set()
    for dept_id, _placeholder in leader_depts:
        if not dept_id or dept_id in seen_dept_ids:
            continue
        seen_dept_ids.add(dept_id)
        dept_name = department_mirror_repo.get_department_name_by_id(db_session, dept_id) or ""
        for scene in (SCENE_DEPARTMENT_DAILY, SCENE_DEPARTMENT_WEEKLY, SCENE_DEPARTMENT_HIGHLIGHTS):
            spec = get_scene(scene)
            if not spec:
                continue
            for variant in spec.variants:
                preview = preview_notification(
                    db_session,
                    scene=scene,
                    variant=variant,
                    department_id=dept_id,
                    department_name=dept_name,
                )
                if not any(str(p.get("user_id") or "") == uid for p in preview.get("eligible") or []):
                    continue
                receive = _preference_receive(
                    prefs, scene=scene, variant=variant, department_id=dept_id
                )
                items.append(
                    {
                        "scene": scene,
                        "variant": variant,
                        "department_id": dept_id,
                        "department_name": dept_name,
                        "title": spec.title,
                        "receive": receive,
                        "opted_out": not receive,
                    }
                )

    seen_keys = {(i["scene"], i["variant"], i["department_id"]) for i in items}
    policy = load_report_push_policy()
    for scene in (SCENE_DEPARTMENT_DAILY, SCENE_DEPARTMENT_WEEKLY, SCENE_DEPARTMENT_HIGHLIGHTS):
        spec = get_scene(scene)
        if not spec:
            continue
        for variant in spec.variants:
            if uid not in set(policy.override_user_ids(scene, variant)):
                continue
            key = (scene, variant, "")
            if key in seen_keys:
                continue
            receive = _preference_receive(prefs, scene=scene, variant=variant, department_id="")
            items.append(
                {
                    "scene": scene,
                    "variant": variant,
                    "department_id": "",
                    "department_name": "",
                    "title": spec.title,
                    "receive": receive,
                    "opted_out": not receive,
                }
            )
    return items
