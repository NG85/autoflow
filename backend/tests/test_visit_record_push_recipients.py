"""visit_record_push_recipients 单元测试。"""

from app.services.visit_record_push_recipients import (
    filter_recipient_list_by_active_open_ids,
)


def test_filter_recipient_list_by_active_open_ids_keeps_active_only():
    recipients = [
        {"open_id": "ou_active", "name": "在职", "type": "leader", "platform": "feishu"},
        {"open_id": "ou_inactive", "name": "停用", "type": "leader", "platform": "feishu"},
    ]

    kept = filter_recipient_list_by_active_open_ids(
        recipients, {"ou_active"}, platform="feishu"
    )

    assert kept == [recipients[0]]
