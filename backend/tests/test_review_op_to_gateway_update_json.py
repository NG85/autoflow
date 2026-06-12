"""review_op_to_gateway_update_json：review 商机回写网关 JSON 映射。"""

from decimal import Decimal

from app.models.wb_review_requests import ReviewOpportunityWritebackOp
from app.services.crm_writeback_service import review_op_to_gateway_update_json


def _op(
    *,
    opportunity_id: str = "opp-001",
    before: dict | None = None,
    after: dict | None = None,
) -> ReviewOpportunityWritebackOp:
    return ReviewOpportunityWritebackOp(
        opportunity_id=opportunity_id,
        before_editable=before or {},
        after_editable=after or {},
    )


def test_only_id_when_editable_fields_unchanged_and_no_writeback_fields():
    payload = review_op_to_gateway_update_json(
        _op(
            before={"forecast_type": "Commit", "forecast_amount": 100.0},
            after={"forecast_type": "Commit", "forecast_amount": 100.0},
        )
    )
    assert payload == {"id": "opp-001", "lostOrderCompetitors": "未知"}


def test_maps_changed_editable_fields_to_camel_case():
    payload = review_op_to_gateway_update_json(
        _op(
            before={
                "opportunity_stage": "stage-a",
                "forecast_type": "Pipeline",
                "expected_closing_date": "2026-03",
                "forecast_amount": Decimal("1000.50"),
            },
            after={
                "opportunity_stage": "stage-b",
                "forecast_type": "Commit",
                "expected_closing_date": "2026-06-15",
                "forecast_amount": 2000,
            },
        )
    )
    assert payload == {
        "id": "opp-001",
        "saleStageId": "stage-b",
        "predictionType": "Commit",
        "expectedSignMonth": "2026-06-15",
        "money": 2000.0,
        "lostOrderCompetitors": "未知",
    }


def test_writeback_only_reason_and_competitor_without_editable_delta():
    payload = review_op_to_gateway_update_json(
        _op(
            before={"opportunity_stage": "lost-stage"},
            after={
                "opportunity_stage": "lost-stage",
                "reason": 8,
                "reasonDesc": "其他说明",
                "lostOrderCompetitors": "competitor-id-42",
            },
        )
    )
    assert payload == {
        "id": "opp-001",
        "reason": 8,
        "reasonDesc": "其他说明",
        "lostOrderCompetitors": "competitor-id-42",
    }


def test_up_pharma_lost_order_submit_includes_stage_reason_and_competitor():
    payload = review_op_to_gateway_update_json(
        _op(
            before={
                "opportunity_stage": "open-stage",
                "forecast_type": "Pipeline",
                "forecast_amount": 50000,
            },
            after={
                "opportunity_stage": "lost-stage-id",
                "forecast_type": "Pipeline",
                "forecast_amount": 50000,
                "reason": 1,
                "reasonDesc": "价格高",
                "lostOrderCompetitors": "cc-001",
            },
        )
    )
    assert payload == {
        "id": "opp-001",
        "saleStageId": "lost-stage-id",
        "reason": 1,
        "reasonDesc": "价格高",
        "lostOrderCompetitors": "cc-001",
    }


def test_reason_desc_snake_case_alias():
    payload = review_op_to_gateway_update_json(
        _op(after={"reason_desc": "  备注  "}),
    )
    assert payload == {"id": "opp-001", "reasonDesc": "备注", "lostOrderCompetitors": "未知"}


def test_competitor_id_snake_case_alias():
    payload = review_op_to_gateway_update_json(
        _op(after={"competitor_id": "cc-snake"}),
    )
    assert payload == {"id": "opp-001", "lostOrderCompetitors": "cc-snake"}


def test_skips_invalid_reason_and_empty_competitor():
    payload = review_op_to_gateway_update_json(
        _op(
            after={
                "reason": "not-a-number",
                "lostOrderCompetitors": "   ",
            },
        )
    )
    assert payload == {"id": "opp-001", "lostOrderCompetitors": "未知"}


def test_defaults_lost_order_competitors_to_unknown_when_not_provided():
    payload = review_op_to_gateway_update_json(
        _op(
            before={"forecast_amount": 1},
            after={"forecast_amount": 2},
        )
    )
    assert payload["lostOrderCompetitors"] == "未知"
    assert payload["money"] == 2.0
