"""follow_up_scope_translator 单元测试。"""

from app.permissions.follow_up_scope_translator import translate_follow_up_scope_to_sql

USER_ID = "550e8400-e29b-41d4-a716-446655440000"
SUBORDINATE_ID = "660e8400-e29b-41d4-a716-446655440001"


def test_empty_filters_denies():
    perm = translate_follow_up_scope_to_sql(None, "OR", user_id=USER_ID)
    assert perm.sql == "1=0"
    assert perm.params == {}


def test_global_enabled_allows_all():
    perm = translate_follow_up_scope_to_sql(
        [{"source": "global", "enabled": True}],
        "OR",
        user_id=USER_ID,
    )
    assert perm.sql == "1=1"


def test_sales_self_creator_only():
    perm = translate_follow_up_scope_to_sql(
        [{"source": "self_creator", "crmId": "crm-user-001"}],
        "OR",
        user_id=USER_ID,
    )
    assert "f.recorder_id" in perm.sql
    assert perm.params["perm_user_id"] == USER_ID.replace("-", "")


def test_org_scope_requires_mapped_user_ids():
    perm = translate_follow_up_scope_to_sql(
        [{"source": "org_scope", "crmUserIds": ["crm-mgr-001"]}],
        "OR",
        user_id=USER_ID,
        org_scope_user_ids=None,
    )
    assert perm.sql == "1=0"


def test_org_scope_with_mapped_user_ids():
    perm = translate_follow_up_scope_to_sql(
        [{"source": "org_scope", "crmUserIds": ["crm-mgr-001"]}],
        "OR",
        user_id=USER_ID,
        org_scope_user_ids=[SUBORDINATE_ID],
    )
    assert "f.recorder_id IN" in perm.sql
    assert perm.params["perm_org_uid_0"] == SUBORDINATE_ID.replace("-", "")
    assert "perm_user_id" not in perm.params


def test_collaborator_filter_skipped_without_exists_sql():
    """collaborator filter 在无 collab_exists_sql 时不拼入 WHERE。"""
    perm = translate_follow_up_scope_to_sql(
        [
            {"source": "self_creator"},
            {"source": "collaborator", "crmId": "crm-user-001"},
        ],
        "OR",
        user_id=USER_ID,
        collab_exists_sql=None,
    )
    assert "follow_up_collab" not in perm.sql
    assert "crm_sales_visit_records.recorder_id" in perm.sql or "f.recorder_id" in perm.sql


def test_collaborator_only_without_exists_sql_denies():
    perm = translate_follow_up_scope_to_sql(
        [{"source": "collaborator", "crmId": "crm-user-001"}],
        "OR",
        user_id=USER_ID,
        collab_exists_sql=None,
    )
    assert perm.sql == "1=0"


def test_linked_crm_disabled_by_default():
    perm = translate_follow_up_scope_to_sql(
        [
            {"source": "self_creator"},
            {"source": "linked_crm", "enabled": False},
        ],
        "OR",
        user_id=USER_ID,
        linked_crm_sql="1=1",
    )
    assert "linked_crm" not in perm.sql.lower() or perm.sql.count("1=1") == 0


def test_linked_crm_enabled_appends_branch():
    linked_sql = "(f.account_id IS NOT NULL)"
    perm = translate_follow_up_scope_to_sql(
        [
            {"source": "self_creator"},
            {"source": "linked_crm", "enabled": True},
        ],
        "OR",
        user_id=USER_ID,
        linked_crm_sql=linked_sql,
    )
    assert linked_sql in perm.sql
