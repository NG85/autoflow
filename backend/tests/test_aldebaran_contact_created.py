"""本地联系人创建后通知 Aldebaran 的入队逻辑测试。"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.api.routes.contacts import notify_aldebaran_local_contact_created
from app.services.aldebaran_service import AldebaranClient


@patch("app.services.aldebaran_service.settings")
def test_trigger_local_contact_created_defaults(mock_settings):
    mock_settings.ALDEBARAN_CONTACT_CREATED_MESSAGE_TYPE = "crm.contact.created"
    mock_settings.ALDEBARAN_MESSAGE_SOURCE_SYSTEM = "crm"

    created_by = uuid4()
    event_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    client = AldebaranClient(session=MagicMock())
    client.submit_incoming_message = MagicMock(return_value={"ok": True})

    client.trigger_local_contact_created(
        contact_id="contact-001",
        customer_id="acc-001",
        created_by_user_id=created_by,
        event_time=event_time,
    )

    kwargs = client.submit_incoming_message.call_args.kwargs
    assert kwargs["message_type"] == "crm.contact.created"
    assert kwargs["source_unique_id"] == "contact-001"
    assert kwargs["source_table"] == "local_contacts"
    assert kwargs["dedupe_key"] == "crm.contact.created:contact-001:v1"
    assert kwargs["trace_id"] == "contact-001"
    assert kwargs["event_time"] == event_time
    assert kwargs["payload"] == {
        "contact_id": "contact-001",
        "customer_id": "acc-001",
        "created_by_user_id": str(created_by),
    }


@patch("app.api.routes.contacts.settings")
def test_notify_skips_existing_contact(mock_settings):
    mock_settings.ALDEBARAN_CONTACT_CREATED_ENABLED = True
    contact = MagicMock(unique_id="contact-001", is_existing=True)

    with patch("app.services.aldebaran_service.aldebaran_client") as client:
        assert notify_aldebaran_local_contact_created(contact) is False
        client.trigger_local_contact_created.assert_not_called()


@patch("app.api.routes.contacts.settings")
def test_notify_skips_when_disabled(mock_settings):
    mock_settings.ALDEBARAN_CONTACT_CREATED_ENABLED = False
    contact = MagicMock(unique_id="contact-001", is_existing=False)

    with patch("app.services.aldebaran_service.aldebaran_client") as client:
        assert notify_aldebaran_local_contact_created(contact) is False
        client.trigger_local_contact_created.assert_not_called()


@patch("app.api.routes.contacts.settings")
def test_notify_triggers_on_new_contact(mock_settings):
    mock_settings.ALDEBARAN_CONTACT_CREATED_ENABLED = True
    user_id = uuid4()
    created_at = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    contact = MagicMock(
        unique_id="contact-001",
        customer_id="acc-001",
        created_by=user_id,
        created_at=created_at,
        is_existing=False,
    )

    with patch("app.services.aldebaran_service.aldebaran_client") as client:
        client.trigger_local_contact_created.return_value = {"ok": True}
        assert notify_aldebaran_local_contact_created(contact, user_id=user_id) is True
        client.trigger_local_contact_created.assert_called_once_with(
            contact_id="contact-001",
            customer_id="acc-001",
            created_by_user_id=user_id,
            event_time=created_at,
        )


@patch("app.api.routes.contacts.settings")
def test_notify_swallows_aldebaran_errors(mock_settings):
    mock_settings.ALDEBARAN_CONTACT_CREATED_ENABLED = True
    contact = MagicMock(
        unique_id="contact-001",
        customer_id="acc-001",
        created_by=None,
        created_at=None,
        is_existing=False,
    )

    with patch("app.services.aldebaran_service.aldebaran_client") as client:
        client.trigger_local_contact_created.side_effect = RuntimeError("down")
        assert notify_aldebaran_local_contact_created(contact) is False
