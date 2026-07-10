"""周跟进实体 forecast_amount / expected_closing_date 相关测试。"""

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from app.api.routes.crm.models import WeeklyFollowupDetailQueryIn
from app.api.routes.crm.weekly_followup.router import _append_weekly_followup_entity_filters
from app.models.crm_weekly_followup_entity_summary import CRMWeeklyFollowupEntitySummary
from app.services.crm_weekly_followup_service import CRMWeeklyFollowupService, _EntityKey
from sqlalchemy import select


def _entity_summary(
    *,
    forecast_amount=None,
    expected_closing_date=None,
    progress="old progress",
) -> CRMWeeklyFollowupEntitySummary:
    return CRMWeeklyFollowupEntitySummary(
        id=uuid4(),
        week_start=date(2026, 5, 10),
        week_end=date(2026, 5, 16),
        department_name="团队A",
        entity_type="opportunity",
        entity_id="opp-1",
        progress=progress,
        forecast_amount=forecast_amount,
        expected_closing_date=expected_closing_date,
    )


def _compile_where(conds: list) -> str:
    stmt = select(CRMWeeklyFollowupEntitySummary).where(*conds)
    return str(
        stmt.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_forecast_opportunity_id_only_for_opportunity_entity():
    svc = CRMWeeklyFollowupService()

    assert (
        svc._forecast_opportunity_id(
            _EntityKey(department_name="团队A", entity_type="opportunity", entity_id="opp-1")
        )
        == "opp-1"
    )
    assert (
        svc._forecast_opportunity_id(
            _EntityKey(department_name="团队A", entity_type="account", entity_id="acc-1")
        )
        is None
    )
    assert (
        svc._forecast_opportunity_id(
            _EntityKey(department_name="团队A", entity_type="partner", entity_id="partner-1")
        )
        is None
    )


def test_append_weekly_followup_entity_filters_forecast_amount_range():
    payload = WeeklyFollowupDetailQueryIn(
        period="2026-W20",
        filter_forecast_amount_min=100000,
        filter_forecast_amount_max=500000,
    )
    conds: list = []
    _append_weekly_followup_entity_filters(MagicMock(), conds, payload)

    sql = _compile_where(conds)
    assert "forecast_amount" in sql
    assert "100000" in sql
    assert "500000" in sql


def test_append_weekly_followup_entity_filters_expected_closing_date_range():
    payload = WeeklyFollowupDetailQueryIn(
        period="2026-W20",
        filter_expected_closing_date_start=date(2026, 3, 1),
        filter_expected_closing_date_end=date(2026, 6, 30),
    )
    conds: list = []
    _append_weekly_followup_entity_filters(MagicMock(), conds, payload)

    sql = _compile_where(conds)
    assert "expected_closing_date" in sql
    assert "2026-03-01" in sql
    assert "2026-06-30" in sql


def test_append_weekly_followup_entity_filters_skips_unset_forecast_filters():
    payload = WeeklyFollowupDetailQueryIn(period="2026-W20")
    conds: list = []
    _append_weekly_followup_entity_filters(MagicMock(), conds, payload)

    assert conds == []


def test_upsert_entity_summary_integrity_error_merges_forecast_fields():
    svc = CRMWeeklyFollowupService()
    existing = _entity_summary()
    incoming = _entity_summary(
        progress="new progress",
        forecast_amount=390000.0,
        expected_closing_date=date(2026, 3, 4),
    )

    session = MagicMock()
    session.exec.side_effect = [MagicMock(first=MagicMock(return_value=None)), MagicMock(first=MagicMock(return_value=existing))]
    session.commit.side_effect = [IntegrityError("insert", {}, Exception("dup")), None]

    result = svc._upsert_entity_summary(session, incoming)

    assert result is existing
    assert existing.progress == "new progress"
    assert existing.forecast_amount == 390000.0
    assert existing.expected_closing_date == date(2026, 3, 4)
    session.rollback.assert_called_once()
    assert session.commit.call_count == 2
    session.refresh.assert_called_once_with(existing)
