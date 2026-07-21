"""_resolve_department_owners_for_todo_stats 集成用例：

验证开启 OAuth 时团队人群 = 负责人汇报链（基础名册） ∪ 当日本部门拜访记录人，
且按 canonical 去重、忽略其他部门 recorder。
"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.crm_statistics_service import CRMStatisticsService

LEADER = UUID("550e8400-e29b-41d4-a716-446655440000")
SUB = UUID("660e8400-e29b-41d4-a716-446655440001")
NEW = UUID("770e8400-e29b-41d4-a716-446655440002")   # 有拜访但不在基础名册
OTHER = UUID("880e8400-e29b-41d4-a716-446655440003")  # 其他部门 recorder

DEPT = "销售一部"


def test_oauth_base_owners_merged_with_same_day_recorders():
    service = CRMStatisticsService()
    session = MagicMock()
    session.info = {}  # 真实 dict，供统计缓存使用

    base_people = {str(LEADER): "王经理", str(SUB): "李下属"}
    base_alias = {
        str(LEADER): str(LEADER),
        "crm-mgr": str(LEADER),
        str(SUB): str(SUB),
        "crm-sub": str(SUB),
    }
    sales_stats = [
        {"department": DEPT, "recorder_id": str(SUB), "recorder": "李下属"},   # 已在基础名册 → 去重
        {"department": DEPT, "recorder_id": str(NEW), "recorder": "新人"},     # 补入
        {"department": "销售二部", "recorder_id": str(OTHER), "recorder": "他部门"},  # 忽略
    ]

    with patch("app.services.crm_statistics_service.settings") as mock_settings:
        mock_settings.REPORT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.permissions.report_scope_service.report_scope_service.resolve_team_owners",
            return_value=(base_people, base_alias),
        ):
            with patch.object(
                CRMStatisticsService,
                "get_sales_daily_statistics",
                return_value=sales_stats,
            ):
                people, alias = service._resolve_department_owners_for_todo_stats(
                    session, DEPT, stat_date=date(2026, 7, 20)
                )

    # 基础名册 ∪ 本部门当日 recorder，去重，忽略他部门
    assert set(people.keys()) == {str(LEADER), str(SUB), str(NEW)}
    assert people[str(NEW)] == "新人"
    assert str(OTHER) not in people
    # 补入的 recorder canonical 自映射
    assert alias[str(NEW)] == str(NEW)


def test_oauth_hit_without_stat_date_keeps_base_only():
    service = CRMStatisticsService()
    session = MagicMock()
    session.info = {}

    base_people = {str(LEADER): "王经理"}
    base_alias = {str(LEADER): str(LEADER)}

    with patch("app.services.crm_statistics_service.settings") as mock_settings:
        mock_settings.REPORT_OAUTH_SCOPE_ENABLED = True
        with patch(
            "app.permissions.report_scope_service.report_scope_service.resolve_team_owners",
            return_value=(base_people, base_alias),
        ):
            with patch.object(
                CRMStatisticsService, "get_sales_daily_statistics"
            ) as get_stats:
                people, _ = service._resolve_department_owners_for_todo_stats(
                    session, DEPT, stat_date=None
                )

    # 无 stat_date 时不并入 recorder，也不查询当日统计
    assert set(people.keys()) == {str(LEADER)}
    get_stats.assert_not_called()
