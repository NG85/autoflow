"""chat_scope_translator 单元测试。"""

from app.permissions.chat_scope_translator import (
    ChatScopeResult,
    translate_chat_scope,
)

USER_ID = "550e8400-e29b-41d4-a716-446655440000"
SUBORDINATE_ID = "660e8400-e29b-41d4-a716-446655440001"


def test_empty_filters_denies():
    result = translate_chat_scope(None, user_id=USER_ID)
    assert result == ChatScopeResult(deny=True)

    result = translate_chat_scope([], user_id=USER_ID)
    assert result.deny is True
    assert result.allow_all is False
    assert result.owner_user_ids == ()


def test_global_enabled_allows_all():
    result = translate_chat_scope(
        [{"source": "global", "enabled": True}],
        user_id=USER_ID,
    )
    assert result.allow_all is True
    assert result.deny is False


def test_global_disabled_does_not_allow_all():
    result = translate_chat_scope(
        [{"source": "global", "enabled": False}],
        user_id=USER_ID,
    )
    assert result.allow_all is False
    assert result.deny is True


def test_self_only_maps_to_current_user():
    result = translate_chat_scope(
        [{"source": "self_only", "crm_id": "crm-user-001"}],
        user_id=USER_ID,
    )
    assert result.allow_all is False
    assert result.deny is False
    assert result.owner_user_ids == (USER_ID,)


def test_self_owner_and_self_creator_also_map_to_self():
    for source in ("self_owner", "self_creator"):
        result = translate_chat_scope([{"source": source}], user_id=USER_ID)
        assert result.owner_user_ids == (USER_ID,), source


def test_org_scope_without_mapped_ids_denies():
    result = translate_chat_scope(
        [{"source": "org_scope", "mode": "team_subordinates", "crm_user_ids": ["crm-mgr"]}],
        user_id=USER_ID,
        org_scope_user_ids=None,
    )
    assert result.deny is True
    assert result.owner_user_ids == ()


def test_org_scope_with_mapped_ids():
    result = translate_chat_scope(
        [{"source": "org_scope", "mode": "team_subordinates"}],
        user_id=USER_ID,
        org_scope_user_ids=[SUBORDINATE_ID],
    )
    # org_scope 无 self 语义，故不含当前用户，仅辖区展开的 users.id
    assert result.owner_user_ids == (SUBORDINATE_ID,)
    assert result.allow_all is False


def test_self_and_org_scope_combined_and_deduped():
    result = translate_chat_scope(
        [
            {"source": "self_only"},
            {"source": "org_scope", "mode": "team_subordinates"},
        ],
        user_id=USER_ID,
        # map_org_scope 通常含本人（include_self=True）；此处含重复应去重保序
        org_scope_user_ids=[USER_ID, SUBORDINATE_ID],
    )
    assert result.owner_user_ids == (USER_ID, SUBORDINATE_ID)


def test_global_wins_over_self_and_org():
    result = translate_chat_scope(
        [
            {"source": "self_only"},
            {"source": "org_scope", "mode": "team_subordinates"},
            {"source": "global", "enabled": True},
        ],
        user_id=USER_ID,
        org_scope_user_ids=[SUBORDINATE_ID],
    )
    assert result.allow_all is True


def test_collaborator_and_linked_crm_are_skipped():
    """history 实体不含 collaborator/linked_crm；即便出现也不产生可见范围。"""
    result = translate_chat_scope(
        [
            {"source": "collaborator", "crm_id": "crm-user-001"},
            {"source": "linked_crm", "enabled": True},
        ],
        user_id=USER_ID,
    )
    assert result.deny is True
    assert result.owner_user_ids == ()
