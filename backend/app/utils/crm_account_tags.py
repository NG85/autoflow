from typing import Any, Iterable, Optional

from pydantic import BaseModel


class AccountTagOption(BaseModel):
    id: str
    name: str


def parse_account_tags(extra: dict[str, Any] | None) -> list[AccountTagOption]:
    if not extra or not isinstance(extra, dict):
        return []
    raw_tags = extra.get("tags")
    if not isinstance(raw_tags, list):
        return []
    out: list[AccountTagOption] = []
    for item in raw_tags:
        if not isinstance(item, dict):
            continue
        tag_id = str(item.get("id") or "").strip()
        tag_name = str(item.get("name") or "").strip()
        if tag_id and tag_name:
            out.append(AccountTagOption(id=tag_id, name=tag_name))
    return out


def merge_distinct_account_tags(tags: Iterable[AccountTagOption]) -> list[AccountTagOption]:
    merged: dict[str, AccountTagOption] = {}
    for tag in tags:
        merged.setdefault(tag.id, tag)
    return sorted(merged.values(), key=lambda item: item.name)


def resolve_followup_account_id(
    account_id: Optional[str],
    partner_id: Optional[str],
) -> Optional[str]:
    aid = (account_id or "").strip()
    if aid:
        return aid
    pid = (partner_id or "").strip()
    return pid or None


def resolve_followup_object_name(
    account_name: Optional[str],
    partner_name: Optional[str],
) -> Optional[str]:
    name = (account_name or "").strip()
    if name:
        return name
    partner = (partner_name or "").strip()
    return partner or None


def resolve_followup_object_id(
    account_id: Optional[str],
    partner_id: Optional[str],
) -> Optional[str]:
    return resolve_followup_account_id(account_id, partner_id)
