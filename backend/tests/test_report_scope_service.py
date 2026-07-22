"""ReportScopeService 单元测试（日报/周报生成阶段团队统计口径）。

锚点：user_department_relation.is_leader 的部门负责人（非 user_profiles 直属上级）。
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.report_scope_service import ReportScopeService

LEADER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
SUB_ID = UUID("660e8400-e29b-41d4-a716-446655440001")


def _service() -> ReportScopeService:
    return ReportScopeService()


def _profile(user_id, name, crm_user_id):
    return SimpleNamespace(user_id=user_id, name=name, crm_user_id=crm_user_id)


def _leader(user_id, crm_user_id):
    return SimpleNamespace(user_id=user_id, crm_user_id=crm_user_id)


def _patches(
    *,
    dept_ids=("dept-1",),
    leader=None,
    org_scope_user_ids=None,
    profiles=None,
    data_scope=None,
    crm_backfill_user_ids=None,
):
    """统一装配 report_scope_service 的外部依赖 mock。"""
    dm_repo = MagicMock()
    dm_repo.get_department_ids_by_name.return_value = list(dept_ids)
    udr_repo = MagicMock()
    udr_repo.get_department_leader.return_value = leader
    up_repo = MagicMock()
    up_repo.get_by_user_ids.return_value = list(profiles or [])

    stack = ExitStack()
    stack.enter_context(
        patch("app.repositories.department_mirror.department_mirror_repo", dm_repo)
    )
    stack.enter_context(
        patch(
            "app.repositories.user_department_relation.user_department_relation_repo",
            udr_repo,
        )
    )
    stack.enter_context(
        patch("app.repositories.user_profile.user_profile_repo", up_repo)
    )
    get_scope = stack.enter_context(
        patch(
            "app.permissions.report_scope_service.oauth_client.get_data_scope",
            return_value=data_scope
            if data_scope is not None
            else {"entity": "daily_report_team", "merge": "OR", "filters": []},
        )
    )
    map_org = stack.enter_context(
        patch(
            "app.permissions.report_scope_service.map_org_scope_from_filters",
            return_value=list(org_scope_user_ids or []),
        )
    )
    map_crm = stack.enter_context(
        patch(
            "app.permissions.report_scope_service.map_crm_user_ids_to_user_ids",
            return_value=list(crm_backfill_user_ids or []),
        )
    )
    return stack, SimpleNamespace(
        dm_repo=dm_repo, udr_repo=udr_repo, up_repo=up_repo,
        get_scope=get_scope, map_org=map_org, map_crm=map_crm,
    )


def test_blank_department_returns_none():
    assert _service().resolve_team_owners(MagicMock(), "  ") is None


def test_no_leader_returns_none():
    stack, m = _patches(leader=None)
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")
    assert result is None
    m.get_scope.assert_not_called()


def test_invalid_leader_user_id_returns_none():
    stack, m = _patches(leader=_leader("not-a-uuid", "crm-mgr"))
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")
    assert result is None
    m.get_scope.assert_not_called()


def test_leader_without_user_id_and_no_backfill_returns_none():
    stack, m = _patches(leader=_leader(None, "crm-mgr"), crm_backfill_user_ids=[])
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")
    assert result is None
    m.map_crm.assert_called_once()
    assert m.map_crm.call_args.args[1] == ["crm-mgr"]
    m.get_scope.assert_not_called()


def test_leader_user_id_backfilled_from_crm_user_id():
    scope = {
        "entity": "daily_report_team",
        "merge": "OR",
        "filters": [{"source": "org_scope", "mode": "team_subordinates"}],
    }
    stack, m = _patches(
        leader=_leader(None, "crm-mgr"),
        crm_backfill_user_ids=[str(LEADER_ID)],
        data_scope=scope,
        org_scope_user_ids=[str(LEADER_ID)],
        profiles=[_profile(LEADER_ID, "王经理", "crm-mgr")],
    )
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")

    assert result is not None
    people, _ = result
    assert people == {str(LEADER_ID): "王经理"}
    # 回补出的 user_id 作为锚点
    m.get_scope.assert_called_once_with(
        user_id=LEADER_ID,
        crm_user_id="crm-mgr",
        entity="daily_report_team",
    )


def test_empty_org_scope_returns_none():
    stack, m = _patches(
        leader=_leader(str(LEADER_ID), "crm-mgr"),
        data_scope={"entity": "daily_report_team", "merge": "OR", "filters": []},
        org_scope_user_ids=[],
    )
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")
    assert result is None


def test_resolves_team_owners_from_org_scope():
    scope = {
        "entity": "daily_report_team",
        "merge": "OR",
        "filters": [{"source": "org_scope", "mode": "team_subordinates"}],
    }
    stack, m = _patches(
        leader=_leader(str(LEADER_ID), "crm-mgr"),
        data_scope=scope,
        org_scope_user_ids=[str(LEADER_ID), str(SUB_ID)],
        profiles=[
            _profile(LEADER_ID, "王经理", "crm-mgr"),
            _profile(SUB_ID, "李下属", "crm-sub"),
        ],
    )
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")

    assert result is not None
    people, alias = result
    assert people == {str(LEADER_ID): "王经理", str(SUB_ID): "李下属"}
    assert alias[str(LEADER_ID)] == str(LEADER_ID)
    assert alias["crm-mgr"] == str(LEADER_ID)
    assert alias["crm-sub"] == str(SUB_ID)
    # 以部门负责人为锚点、指定团队日报实体
    m.get_scope.assert_called_once_with(
        user_id=LEADER_ID,
        crm_user_id="crm-mgr",
        entity="daily_report_team",
    )


def test_weekly_entity_is_passed_through():
    scope = {
        "entity": "weekly_report_team",
        "merge": "OR",
        "filters": [{"source": "org_scope", "mode": "team_subordinates"}],
    }
    stack, m = _patches(
        leader=_leader(str(LEADER_ID), "crm-mgr"),
        data_scope=scope,
        org_scope_user_ids=[str(LEADER_ID)],
        profiles=[_profile(LEADER_ID, "王经理", "crm-mgr")],
    )
    with stack:
        result = _service().resolve_team_owners(
            MagicMock(), "销售一部", entity="weekly_report_team"
        )

    assert result is not None
    assert m.get_scope.call_args.kwargs["entity"] == "weekly_report_team"


def test_resolved_ids_without_profiles_returns_none():
    scope = {
        "entity": "daily_report_team",
        "merge": "OR",
        "filters": [{"source": "org_scope", "mode": "team_subordinates"}],
    }
    stack, m = _patches(
        leader=_leader(str(LEADER_ID), "crm-mgr"),
        data_scope=scope,
        org_scope_user_ids=[str(SUB_ID)],
        profiles=[],
    )
    with stack:
        result = _service().resolve_team_owners(MagicMock(), "销售一部")
    assert result is None
