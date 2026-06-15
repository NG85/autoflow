"""多维表格按 record_id 补偿 upsert 逻辑测试。"""

from unittest.mock import MagicMock, patch

from app.tasks.bitable_import import (
    _extract_bitable_unique_id_field_value,
    build_bitable_records_with_unique_id,
    push_crm_rows_to_bitable_upsert,
    split_bitable_upsert_records,
)


def test_extract_bitable_unique_id_field_value():
    assert _extract_bitable_unique_id_field_value(" link_a ") == "link_a"
    assert _extract_bitable_unique_id_field_value([{"text": "link_b"}]) == "link_b"
    assert _extract_bitable_unique_id_field_value(None) is None


def test_split_bitable_upsert_records():
    records = [
        ("link_a", {"唯一ID": "link_a", "跟进记录": "a"}),
        ("link_b", {"唯一ID": "link_b", "跟进记录": "b"}),
    ]
    to_create, to_update = split_bitable_upsert_records(
        records,
        {"link_a": "bitable_rec_1"},
    )
    assert to_create == [{"唯一ID": "link_b", "跟进记录": "b"}]
    assert to_update == [("bitable_rec_1", {"唯一ID": "link_a", "跟进记录": "a"})]


def test_build_bitable_records_with_unique_id():
    row = MagicMock()
    row._mapping = {
        "record_id": "link_test_001",
        "account_name": "客户A",
        "visit_type": "link",
        "recorder": "张三",
    }
    with patch("app.tasks.bitable_import.build_bitable_fields_from_crm_row") as mock_build:
        mock_build.return_value = {"唯一ID": "link_test_001", "客户名称": "客户A"}
        result = build_bitable_records_with_unique_id([row])
    assert result == [("link_test_001", {"唯一ID": "link_test_001", "客户名称": "客户A"})]


@patch("app.tasks.bitable_import.batch_create_bitable_records")
@patch("app.tasks.bitable_import.batch_update_bitable_records")
@patch("app.tasks.bitable_import.search_bitable_records_by_unique_ids")
@patch("app.tasks.bitable_import.resolve_bitable_app_token", return_value="app_tok")
def test_push_crm_rows_to_bitable_upsert(
    _resolve,
    mock_search,
    mock_update,
    mock_create,
):
    mock_search.return_value = {"link_exist": "rec_exist"}
    mock_update.return_value = ["rec_exist"]
    mock_create.return_value = ["rec_new"]

    row_exist = MagicMock()
    row_exist._mapping = {"record_id": "link_exist", "account_name": "A"}
    row_new = MagicMock()
    row_new._mapping = {"record_id": "link_new", "account_name": "B"}

    def _fields(row_dict):
        return {
            "唯一ID": row_dict["record_id"],
            "客户名称": row_dict.get("account_name"),
        }

    client = MagicMock()
    client.get_tenant_access_token.return_value = "tok"

    with patch("app.tasks.bitable_import.build_bitable_fields_from_crm_row", side_effect=_fields):
        result = push_crm_rows_to_bitable_upsert(
            [row_exist, row_new],
            platform="feishu",
            url_type="base",
            url_token="base_tok",
            table_id="tbl",
            client=client,
            range_desc="test",
        )

    assert result == ["rec_exist", "rec_new"]
    mock_search.assert_called_once()
    mock_update.assert_called_once()
    mock_create.assert_called_once()
    _, create_kwargs = mock_create.call_args
    assert create_kwargs["records_fields"] == [{"唯一ID": "link_new", "客户名称": "B"}]
