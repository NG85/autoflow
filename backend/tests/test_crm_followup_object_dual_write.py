"""Tests for followup_object dual-write into legacy account/partner slots."""

from app.utils.crm_followup_object import apply_followup_object_legacy_dual_write


def test_dual_write_end_customer_fills_account_slots():
    payload = {
        "followup_object_type": "end_customer",
        "followup_object_id": "acc-1",
        "followup_object_name": "客户甲",
    }
    apply_followup_object_legacy_dual_write(payload)
    assert payload["account_id"] == "acc-1"
    assert payload["account_name"] == "客户甲"
    assert "partner_id" not in payload
    assert "partner_name" not in payload


def test_dual_write_partner_fills_partner_slots():
    payload = {
        "followup_object_type": "partner",
        "followup_object_id": "p-1",
        "followup_object_name": "伙伴乙",
    }
    apply_followup_object_legacy_dual_write(payload)
    assert payload["partner_id"] == "p-1"
    assert payload["partner_name"] == "伙伴乙"
    assert "account_id" not in payload
    assert "account_name" not in payload


def test_dual_write_lead_does_not_touch_legacy_slots():
    payload = {
        "followup_object_type": "lead",
        "followup_object_id": "lead-1",
        "followup_object_name": "线索丙",
    }
    apply_followup_object_legacy_dual_write(payload)
    assert "account_id" not in payload
    assert "account_name" not in payload
    assert "partner_id" not in payload
    assert "partner_name" not in payload


def test_dual_write_does_not_overwrite_existing_legacy_values():
    payload = {
        "followup_object_type": "end_customer",
        "followup_object_id": "acc-new",
        "followup_object_name": "新名称",
        "account_id": "acc-old",
        "account_name": "旧名称",
    }
    apply_followup_object_legacy_dual_write(payload)
    assert payload["account_id"] == "acc-old"
    assert payload["account_name"] == "旧名称"
