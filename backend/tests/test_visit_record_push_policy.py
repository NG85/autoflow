"""拜访卡片推送策略：按角色组合 legacy / recap_lite。"""

from app.services.visit_record_push_policy import (
    CARD_LEGACY,
    CARD_RECAP_LITE,
    DEFAULT_VISIT_RECORD_PUSH_POLICY,
    filter_recipients_by_policy,
    parse_visit_record_push_policy,
)


def test_empty_config_is_all_legacy():
    policy = parse_visit_record_push_policy(None)
    assert policy == DEFAULT_VISIT_RECORD_PUSH_POLICY
    assert policy.recipient_card("recorder") == CARD_LEGACY
    assert policy.recipient_card("leader") == CARD_LEGACY
    assert policy.recipient_enabled("configured_cc") is True
    assert policy.uses_recap_lite() is False


def test_per_role_dict_mixes_cards():
    policy = parse_visit_record_push_policy(
        {
            "recipients": {
                "recorder": {"enabled": True, "card": "recap_lite"},
                "leader": {"enabled": True, "card": "legacy"},
                "configured_cc": {"enabled": True, "card": "legacy"},
                "collaborative_participant": {"enabled": True, "card": "recap_lite"},
            },
            "groups": {
                "review": {"enabled": True, "card": "legacy"},
                "brief": False,
            },
        }
    )
    assert policy.recipient_card("recorder") == CARD_RECAP_LITE
    assert policy.recipient_card("leader") == CARD_LEGACY
    assert policy.recipient_card("configured_cc") == CARD_LEGACY
    assert policy.recipient_card("collaborative_participant") == CARD_RECAP_LITE
    assert policy.group_card("review") == CARD_LEGACY
    assert policy.group_enabled("brief") is False
    assert policy.uses_recap_lite() is True


def test_compact_string_and_bool_forms():
    policy = parse_visit_record_push_policy(
        {
            "recorder": "recap_lite",
            "leader": "legacy",
            "configured_cc": False,
            "collaborative_participant": True,
        }
    )
    assert policy.recipient_card("recorder") == CARD_RECAP_LITE
    assert policy.recipient_card("leader") == CARD_LEGACY
    assert policy.recipient_enabled("configured_cc") is False
    assert policy.recipient_enabled("collaborative_participant") is True
    assert policy.recipient_card("collaborative_participant") == CARD_LEGACY


def test_strategy_fills_unspecified_roles_and_role_override_wins():
    policy = parse_visit_record_push_policy(
        {
            "strategy": "recap_lite",
            "recipients": {
                "leader": "legacy",
                "configured_cc": False,
            },
        }
    )
    assert policy.recipient_card("recorder") == CARD_RECAP_LITE
    assert policy.recipient_card("collaborative_participant") == CARD_RECAP_LITE
    assert policy.recipient_card("leader") == CARD_LEGACY
    assert policy.recipient_enabled("configured_cc") is False
    assert policy.group_card("review") == CARD_RECAP_LITE


def test_all_recap_lite_via_strategy_only():
    policy = parse_visit_record_push_policy({"strategy": "recap_lite"})
    assert policy.recipient_card("recorder") == CARD_RECAP_LITE
    assert policy.recipient_card("leader") == CARD_RECAP_LITE
    assert policy.group_card("review") == CARD_RECAP_LITE
    assert policy.uses_recap_lite() is True


def test_unknown_card_falls_back_to_legacy():
    policy = parse_visit_record_push_policy(
        {"recipients": {"recorder": {"card": "unknown_card"}}}
    )
    assert policy.recipient_card("recorder") == CARD_LEGACY


def test_filter_recipients_drops_disabled_roles():
    policy = parse_visit_record_push_policy(
        {"recipients": {"leader": False, "configured_cc": False}}
    )
    filtered = filter_recipients_by_policy(
        {
            "feishu": [
                {"open_id": "ou_1", "type": "recorder", "name": "销售"},
                {"open_id": "ou_2", "type": "leader", "name": "上级"},
                {"open_id": "ou_3", "type": "configured_cc", "name": "抄送"},
            ]
        },
        policy,
    )
    assert [r["type"] for r in filtered["feishu"]] == ["recorder"]
