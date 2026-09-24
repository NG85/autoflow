"""只读 crm_postvisit_extract_items：销售视角复盘结构化抽取。"""

from unittest.mock import MagicMock

from app.services.visit_record_extract_reader import (
    DEFAULT_PROFILE_ID,
    build_extract_response,
    load_visit_record_extract_response,
)


def _row(**overrides):
    base = {
        "unique_id": "ext-1",
        "visit_id": "rec-1",
        "item_type": "KEY_GAP",
        "extract_key": "cfo",
        "payload": {
            "claim": "CFO尚未覆盖",
            "why": "未接触",
            "confidence": 0.8,
            "evidence": [],
            "current_state": "未接触",
        },
        "severity": "MEDIUM",
        "profile_id": DEFAULT_PROFILE_ID,
        "status": "PENDING",
    }
    base.update(overrides)
    return base


def test_card_links_hide_potential_opp_when_empty():
    data = build_extract_response(
        visit_id="rec-1",
        rows=[
            _row(),
            _row(
                unique_id="ext-2",
                item_type="SUGGESTED_ACTION",
                extract_key="act",
                payload={
                    "claim": "确认CFO参与方式",
                    "why": "下一阶段需要",
                    "confidence": 0.7,
                    "evidence": [],
                    "origin": "ai_recommendation",
                    "priority": "P1",
                },
            ),
        ],
    )
    keys = [link["key"] for link in data["card_links"]]
    assert "follow_ups" in keys
    assert "key_issues" in keys
    assert "next_visit" not in keys
    assert "potential_opps" not in keys
    assert "KEY_GAP" in data["items"]
    assert data["items"]["KEY_GAP"][0]["current_state"] == "未接触"
    assert "POTENTIAL_OPP" not in data["items"]


def test_suggested_action_sorts_commitment_first():
    data = build_extract_response(
        visit_id="rec-2",
        rows=[
            _row(
                unique_id="ai",
                item_type="SUGGESTED_ACTION",
                extract_key="ai",
                payload={
                    "claim": "AI建议",
                    "why": "w",
                    "confidence": 0.99,
                    "evidence": [],
                    "origin": "ai_recommendation",
                    "priority": "P1",
                },
            ),
            _row(
                unique_id="commit",
                item_type="SUGGESTED_ACTION",
                extract_key="commit",
                payload={
                    "claim": "周五报价",
                    "why": "已承诺",
                    "confidence": 0.5,
                    "evidence": [],
                    "origin": "sales_commitment",
                    "priority": "P1",
                    "due_hint": "本周五",
                },
            ),
        ],
    )
    claims = [row["claim"] for row in data["items"]["SUGGESTED_ACTION"]]
    assert claims[0] == "周五报价"


def test_card_links_split_follow_ups_and_next_visit():
    data = build_extract_response(
        visit_id="rec-nv",
        rows=[
            _row(
                unique_id="plan",
                item_type="NEXT_VISIT_PLAN",
                extract_key="next",
                payload={
                    "claim": "下次确认CFO参与方式",
                    "why": "审批路径未闭环",
                    "confidence": 0.7,
                    "evidence": [],
                    "must_confirm": ["CFO是否出席"],
                },
            )
        ],
    )
    keys = [link["key"] for link in data["card_links"]]
    assert keys == ["next_visit"]
    assert data["card_links"][0]["title"] == "下次见面"


def test_payload_json_string_is_parsed():
    data = build_extract_response(
        visit_id="rec-3",
        rows=[
            _row(
                payload='{"claim": "缺口", "why": "w", "confidence": 0.4, "evidence": []}',
            )
        ],
    )
    assert data["items"]["KEY_GAP"][0]["claim"] == "缺口"


def test_card_links_snapshot_overrides_fallback_profile():
    data = build_extract_response(
        visit_id="rec-snap",
        rows=[
            _row(
                card_links=[
                    {
                        "key": "only_issues",
                        "title": "快照入口",
                        "item_types": ["KEY_GAP"],
                        "show_if_any": True,
                    }
                ]
            )
        ],
    )
    assert len(data["card_links"]) == 1
    assert data["card_links"][0]["key"] == "only_issues"
    assert data["card_links"][0]["title"] == "快照入口"


def test_load_extract_response_query_params(monkeypatch):
    rows = [_row()]
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session = MagicMock()
    session.execute.return_value = result
    monkeypatch.setattr(
        "app.services.visit_record_extract_reader.settings.ALDEBARAN_POSTVISIT_EXTRACT_TABLE",
        "crm_postvisit_extract_items",
    )

    data = load_visit_record_extract_response(session, "rec-1")
    assert data["visit_id"] == "rec-1"
    assert data["profile_id"] == DEFAULT_PROFILE_ID
    assert "KEY_GAP" in data["items"]

    sql, params = session.execute.call_args.args[0], session.execute.call_args.args[1]
    assert "crm_postvisit_extract_items" in str(sql)
    assert params["visit_id"] == "rec-1"
    assert params["viewer_role"] == "sales"
    assert params["superseded"] == "SUPERSEDED"


def test_load_extract_response_returns_empty_on_error():
    session = MagicMock()
    session.execute.side_effect = RuntimeError("table missing")
    empty = load_visit_record_extract_response(session, "rec-1")
    assert empty["visit_id"] == "rec-1"
    assert empty["items"] == {}
    assert empty["card_links"] == []
    assert load_visit_record_extract_response(None, "rec-1")["items"] == {}
    assert load_visit_record_extract_response(session, "")["visit_id"] == ""
