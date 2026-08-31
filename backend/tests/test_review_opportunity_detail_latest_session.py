"""无 session_id 时，商机详情只借最新可见 session 取上下文，不按参会人自有快照裁剪。"""

from unittest.mock import MagicMock, patch

from app.services.crm_review_service import crm_review_service

USER_ID = "11111111-1111-1111-1111-111111111111"


def test_without_session_id_skips_attendee_owner_filter():
    db = MagicMock()
    latest_risk = MagicMock()
    latest_risk.snapshot_period = "2026-W35"
    latest_risk.session_id = "session-1"
    db.exec.return_value.first.return_value = latest_risk

    built = {"session_id": "session-1", "opportunity_id": "opp-1"}
    with (
        patch(
            "app.services.crm_review_service.resolve_review_session_view_scope",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.crm_review_service.apply_review_session_list_filter",
            side_effect=lambda stmt, scope, uid: stmt,
        ),
        patch.object(
            crm_review_service,
            "get_opportunity_risk_progress_details",
        ) as scoped,
        patch.object(
            crm_review_service,
            "_build_opportunity_risk_progress_details",
            return_value=built,
        ) as build,
    ):
        result = crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
            db,
            opportunity_id="opp-1",
            user_id=USER_ID,
        )

    assert result is built
    build.assert_called_once_with(
        db,
        session_id="session-1",
        snapshot_period="2026-W35",
        opportunity_id="opp-1",
    )
    scoped.assert_not_called()


def test_with_session_id_still_uses_session_scope_owner_filter():
    db = MagicMock()
    session = MagicMock()
    session.period = "2026-W35"
    session.unique_id = "session-1"

    scoped_result = {"session_id": "session-1", "opportunity_id": "opp-1"}
    with (
        patch(
            "app.services.crm_review_service.resolve_review_session_view_scope",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.crm_review_service.crm_review_session_repo.get_by_unique_id",
            return_value=session,
        ),
        patch(
            "app.services.crm_review_service.user_can_access_review_session",
            return_value=True,
        ),
        patch.object(
            crm_review_service,
            "get_opportunity_risk_progress_details",
            return_value=scoped_result,
        ) as scoped,
        patch.object(
            crm_review_service,
            "_build_opportunity_risk_progress_details",
        ) as build,
    ):
        result = crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
            db,
            opportunity_id="opp-1",
            session_id="session-1",
            user_id=USER_ID,
        )

    assert result is scoped_result
    scoped.assert_called_once_with(
        db,
        session_id="session-1",
        user_id=USER_ID,
        opportunity_id="opp-1",
    )
    build.assert_not_called()
