from datetime import date
from unittest.mock import MagicMock

import pytest

from app.services.aldebaran_service import (
    AldebaranClient,
    normalize_opportunity_forecast_batch_response,
    parse_aldebaran_closing_date,
)


def test_normalize_opportunity_forecast_batch_response_aldebaran_example():
    body = {
        "message": "成功获取商机金额信息",
        "status": "success",
        "data": {
            "opportunities": [
                {
                    "unique_id": "67300b3b6f82230001cf9291",
                    "forecast_amount": 390000.0,
                    "expected_closing_date": "2026-03-04",
                    "amount_field": "estimated_acv",
                },
                {
                    "unique_id": "69e8390958725d000771a12c",
                    "forecast_amount": 200000.0,
                    "expected_closing_date": "2026-06-29",
                    "amount_field": "estimated_acv",
                },
            ]
        },
        "error": None,
    }

    result = normalize_opportunity_forecast_batch_response(body)

    assert result["67300b3b6f82230001cf9291"].forecast_amount == 390000.0
    assert result["67300b3b6f82230001cf9291"].expected_closing_date == date(2026, 3, 4)
    assert result["69e8390958725d000771a12c"].forecast_amount == 200000.0
    assert result["69e8390958725d000771a12c"].expected_closing_date == date(2026, 6, 29)


def test_normalize_opportunity_forecast_batch_response_invalid_payload():
    assert normalize_opportunity_forecast_batch_response(None) == {}
    assert normalize_opportunity_forecast_batch_response({"status": "success"}) == {}
    assert normalize_opportunity_forecast_batch_response({"data": {"opportunities": "bad"}}) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-04", date(2026, 3, 4)),
        ("2026-03-04T00:00:00", date(2026, 3, 4)),
        (date(2026, 6, 29), date(2026, 6, 29)),
        ("2026-03", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_aldebaran_closing_date(raw, expected):
    assert parse_aldebaran_closing_date(raw) == expected


def test_query_opportunity_amount_posts_ids_and_parses_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "opportunities": [
                {
                    "unique_id": "opp-1",
                    "forecast_amount": 120000.0,
                    "expected_closing_date": "2026-06-15",
                }
            ]
        },
    }
    mock_resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.post.return_value = mock_resp

    client = AldebaranClient(
        session=session,
        base_url="http://aldebaran:8000",
        opportunity_query_amount_path="/api/v1/opportunity/query/amount",
    )
    result = client.query_opportunity_amount(["opp-1", "opp-1", ""])

    session.post.assert_called_once()
    assert session.post.call_args.kwargs["json"] == {"opportunity_ids": ["opp-1"]}
    assert session.post.call_args.args[0] == "http://aldebaran:8000/api/v1/opportunity/query/amount"
    assert result["opp-1"].forecast_amount == 120000.0
    assert result["opp-1"].expected_closing_date == date(2026, 6, 15)


def test_query_opportunity_amount_empty_ids_short_circuits():
    client = AldebaranClient(session=MagicMock(), base_url="http://aldebaran:8000")
    assert client.query_opportunity_amount([]) == {}
    client._session.post.assert_not_called()


def test_query_opportunity_amount_raises_on_non_success_status():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "error", "message": "failed"}
    mock_resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.post.return_value = mock_resp

    client = AldebaranClient(session=session, base_url="http://aldebaran:8000")
    with pytest.raises(RuntimeError, match="invalid response"):
        client.query_opportunity_amount(["opp-1"])
