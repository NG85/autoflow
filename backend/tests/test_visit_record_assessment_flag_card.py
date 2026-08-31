"""拜访卡片推送：assessment_flag 转为红/黄/绿 icon。"""

from unittest.mock import MagicMock, patch

import pytest

from app.crm.save_engine import (
    _convert_assessment_flag_for_card,
    fill_sales_visit_record_fields,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("red", "🔴"),
        ("YELLOW", "🟡"),
        (" Green ", "🟢"),
        ("🔴", "🔴"),
        (None, None),
        ("", None),
        ("unknown", "unknown"),
    ],
)
def test_convert_assessment_flag_for_card(raw, expected):
    assert _convert_assessment_flag_for_card(raw) == expected


@patch(
    "app.services.crm_config_service.add_field_mapping_to_data",
    side_effect=lambda data, *_args, **_kwargs: data,
)
def test_fill_sales_visit_record_fields_converts_assessment_flag(_mock_mapping):
    record = {
        "account_name": "Acme",
        "assessment_flag": "red",
        "is_first_visit": False,
        "is_call_high": False,
    }
    filled = fill_sales_visit_record_fields(record, MagicMock())
    assert filled["assessment_flag"] == "🔴"


@patch(
    "app.services.crm_config_service.add_field_mapping_to_data",
    side_effect=lambda data, *_args, **_kwargs: data,
)
def test_fill_sales_visit_record_fields_keeps_empty_assessment_flag(_mock_mapping):
    record = {
        "account_name": "Acme",
        "assessment_flag": None,
        "is_first_visit": False,
        "is_call_high": False,
    }
    filled = fill_sales_visit_record_fields(record, MagicMock())
    assert filled["assessment_flag"] is None
