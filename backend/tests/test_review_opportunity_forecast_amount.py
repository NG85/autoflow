"""无 session_id 时，商机详情 forecast_amount 按 CalculationFieldMapping.acv_field 取值。"""

from unittest.mock import MagicMock, patch

from app.models.crm_opportunities import CRMOpportunity
from app.services.crm_review_service import (
    _resolve_opportunity_forecast_amount,
    crm_review_service,
)


def _opp(*, tcv=100000, acv=20000) -> CRMOpportunity:
    return CRMOpportunity(
        unique_id="opp-1",
        estimated_tcv=tcv,
        estimated_acv=acv,
        is_channel_reported_opportunity="否",
    )


def test_resolve_forecast_amount_default_estimated_tcv():
    db = MagicMock()
    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "estimated_tcv"
        assert _resolve_opportunity_forecast_amount(db, _opp()) == 100000.0
        mock_cfg.return_value.get_config_value.assert_called_once_with(
            "CalculationFieldMapping",
            "acv_field",
            default_value="estimated_tcv",
        )


def test_resolve_forecast_amount_uses_mapped_field():
    db = MagicMock()
    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "estimated_acv"
        assert _resolve_opportunity_forecast_amount(db, _opp()) == 20000.0


def test_resolve_forecast_amount_invalid_mapping_falls_back_to_estimated_tcv():
    db = MagicMock()
    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "not_a_column"
        assert _resolve_opportunity_forecast_amount(db, _opp()) == 100000.0


def test_resolve_forecast_amount_none_when_mapped_value_missing():
    db = MagicMock()
    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "estimated_tcv"
        assert _resolve_opportunity_forecast_amount(db, _opp(tcv=None)) is None


def _exec_results(*first_values):
    results = []
    for value in first_values:
        result = MagicMock()
        result.first.return_value = value
        results.append(result)
    return results


def test_detail_without_session_id_uses_mapped_forecast_amount():
    db = MagicMock()
    db.exec.side_effect = _exec_results(None, _opp(tcv=390000, acv=120000))

    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "estimated_tcv"
        result = crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
            db,
            opportunity_id="opp-1",
        )

    assert result["session_id"] == ""
    assert result["snapshot_basic"]["forecast_amount"] == 390000.0


def test_detail_without_session_id_uses_acv_when_mapping_points_to_estimated_acv():
    db = MagicMock()
    db.exec.side_effect = _exec_results(None, _opp(tcv=390000, acv=120000))

    with patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg:
        mock_cfg.return_value.get_config_value.return_value = "estimated_acv"
        result = crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
            db,
            opportunity_id="opp-1",
        )

    assert result["snapshot_basic"]["forecast_amount"] == 120000.0


def test_detail_with_unknown_session_id_does_not_map_forecast_amount():
    db = MagicMock()
    db.exec.side_effect = _exec_results(_opp(tcv=390000))

    with (
        patch(
            "app.services.crm_review_service.crm_review_session_repo.get_by_unique_id",
            return_value=None,
        ),
        patch("app.services.crm_review_service.get_crm_config_service") as mock_cfg,
    ):
        mock_cfg.return_value.get_config_value.return_value = "estimated_tcv"
        result = crm_review_service.get_opportunity_risk_progress_details_by_latest_session(
            db,
            opportunity_id="opp-1",
            session_id="missing-session",
        )

    assert result["session_id"] == "missing-session"
    assert result["snapshot_basic"]["forecast_amount"] is None
    mock_cfg.assert_not_called()
