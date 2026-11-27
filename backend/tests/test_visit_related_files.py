"""拜访记录相关文件资料。"""

import pytest
from pydantic import ValidationError

from unittest.mock import MagicMock, patch

from app.api.routes.crm.models import CompleteVisitRecordCreate
from app.crm.save_engine import fill_sales_visit_record_fields
from app.utils.visit_record_export import (
    UnknownVisitRecordExportColumn,
    VISIT_RECORD_EXPORT_COLUMN_KEYS,
    build_export_row,
    resolve_export_columns,
)


def test_related_files_stores_url_as_given():
    record = CompleteVisitRecordCreate(
        form_type="complete",
        related_files=[
            {"name": "方案.pdf", "url": "https://example.com/a.pdf"},
            {"name": "纪要.docx", "url": "data/customer-uploads/notes.docx"},
            {"url": "  notes.docx  "},
        ],
    )
    assert [item.model_dump() for item in record.related_files] == [
        {"name": "方案.pdf", "url": "https://example.com/a.pdf"},
        {"name": "纪要.docx", "url": "data/customer-uploads/notes.docx"},
        {"name": None, "url": "  notes.docx  "},
    ]


@pytest.mark.parametrize(
    "related_files",
    [
        "https://example.com/a.pdf",
        '[{"name":"报价.xlsx","url":"https://example.com/c.xlsx"}]',
        {"name": "单文件", "url": "https://example.com/one"},
        ["https://example.com/b.docx"],
        [{"name": "只有文件名"}],
        1,
    ],
)
def test_related_files_rejects_shapes_outside_the_object_array(related_files):
    with pytest.raises(ValidationError):
        CompleteVisitRecordCreate(form_type="complete", related_files=related_files)


def test_create_model_keeps_related_files_separate_from_attachment():
    record = CompleteVisitRecordCreate(
        form_type="complete",
        attachment={"url": "https://example.com/photo.jpg", "location": "上海"},
        related_files=[{"name": "方案.pdf", "url": "https://example.com/a.pdf"}],
    )
    dumped = record.model_dump(exclude_none=True)
    assert dumped["attachment"]["location"] == "上海"
    assert dumped["related_files"] == [
        {"name": "方案.pdf", "url": "https://example.com/a.pdf"}
    ]


@patch(
    "app.services.crm_config_service.add_field_mapping_to_data",
    side_effect=lambda data, *_args, **_kwargs: data,
)
def test_card_payload_omits_related_files(_mock_mapping):
    filled = fill_sales_visit_record_fields(
        {
            "account_name": "Acme",
            "is_first_visit": False,
            "is_call_high": False,
            "related_files": [
                {"name": "方案.pdf", "url": "https://example.com/a.pdf"},
                {"name": "纪要.docx", "url": "data/customer-uploads/notes.docx"},
            ],
        },
        MagicMock(),
    )
    assert "related_files" not in filled


def test_export_ignores_related_files():
    from tests.test_visit_record_export_columns import _export_item

    assert "related_files" not in VISIT_RECORD_EXPORT_COLUMN_KEYS
    row = build_export_row(
        _export_item(
            related_files=[
                {"name": "方案.pdf", "url": "https://example.com/a.pdf"},
            ]
        ),
        language="zh",
    )
    assert "related_files" not in row
    with pytest.raises(UnknownVisitRecordExportColumn):
        resolve_export_columns(["related_files"])
