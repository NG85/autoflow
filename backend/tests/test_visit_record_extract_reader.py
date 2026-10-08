"""crm_postvisit_extract_items：销售视角复盘结构化抽取，以及赞踩、采纳、拒绝。"""

import logging
from unittest.mock import MagicMock

from app.services.visit_record_extract_reader import (
    DEFAULT_PROFILE_ID,
    build_extract_response,
    load_visit_record_extract_response,
    save_visit_record_extract_decisions,
    save_visit_record_extract_feedback,
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
    assert data["card_links"][0]["title"] == "下次沟通"


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


def test_load_extract_response_query_params():
    rows = [_row()]
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.exec.return_value = result

    data = load_visit_record_extract_response(session, "rec-1")
    assert data["visit_id"] == "rec-1"
    assert data["profile_id"] == DEFAULT_PROFILE_ID
    assert "KEY_GAP" in data["items"]

    statement = session.exec.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "crm_postvisit_extract_items" in sql
    assert "rec-1" in sql
    assert "sales" in sql
    assert "SUPERSEDED" in sql


def test_load_extract_response_returns_empty_on_error():
    session = MagicMock()
    session.exec.side_effect = RuntimeError("table missing")
    empty = load_visit_record_extract_response(session, "rec-1")
    assert empty["visit_id"] == "rec-1"
    assert empty["items"] == {}
    assert empty["card_links"] == []
    assert load_visit_record_extract_response(None, "rec-1")["items"] == {}
    assert load_visit_record_extract_response(session, "")["visit_id"] == ""


def test_serialize_includes_feedback():
    data = build_extract_response(
        visit_id="rec-1",
        rows=[
            _row(
                feedback="up",
                feedback_comment="有用",
                feedback_user_id="user-1",
                feedback_at="2026-10-08T03:00:00",
            )
        ],
        viewer_user_id="USER-1",
    )
    item = data["items"]["KEY_GAP"][0]
    assert item["feedback"] == "up"
    assert item["feedback_comment"] == "有用"
    assert item["feedback_user_id"] == "user-1"
    assert item["feedback_at"] == "2026-10-08T03:00:00"


def test_serialize_hides_other_users_feedback():
    data = build_extract_response(
        visit_id="rec-1",
        rows=[
            _row(
                feedback="down",
                feedback_comment="别人的意见",
                feedback_user_id="11111111-1111-1111-1111-111111111111",
                feedback_at="2026-10-08T03:00:00",
            )
        ],
        viewer_user_id="22222222222222222222222222222222",
    )
    item = data["items"]["KEY_GAP"][0]
    assert item["feedback"] is None
    assert item["feedback_comment"] is None
    assert item["feedback_user_id"] is None
    assert item["feedback_at"] is None
    assert item["claim"] == "CFO尚未覆盖"


class _StoredItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_save_extract_feedback_updates_existing_row(caplog):
    session = MagicMock()
    current = _StoredItem(
        feedback="up",
        feedback_comment="旧备注",
        feedback_user_id="user-1",
        feedback_at="2026-10-01T00:00:00",
    )
    session.exec.return_value.first.return_value = current
    caplog.set_level(logging.INFO)

    saved = save_visit_record_extract_feedback(
        session,
        visit_id="rec-1",
        unique_id="ext-1",
        feedback="down",
        feedback_comment="  不太准  ",
        user_id="user-9",
    )

    assert saved["feedback"] == "down"
    assert saved["feedback_comment"] == "不太准"
    assert saved["feedback_user_id"] == "user-9"
    assert saved["feedback_at"]
    assert current.feedback == "down"
    assert current.feedback_comment == "不太准"
    assert current.feedback_user_id == "user-9"
    session.add.assert_called_once_with(current)
    session.commit.assert_called_once()
    assert "visit_extract_interaction" in caplog.text
    assert "kind=feedback" in caplog.text
    assert "user-1" in caplog.text
    assert "不太准" in caplog.text


def test_save_extract_feedback_clear_and_missing():
    session = MagicMock()
    current = _StoredItem(
        feedback="up",
        feedback_comment=None,
        feedback_user_id="user-9",
        feedback_at=None,
    )
    session.exec.return_value.first.return_value = current
    cleared = save_visit_record_extract_feedback(
        session,
        visit_id="rec-1",
        unique_id="ext-1",
        feedback=None,
        feedback_comment="忽略",
        user_id="user-9",
    )
    assert cleared["feedback"] is None
    assert cleared["feedback_comment"] is None
    assert cleared["feedback_at"] is None
    assert current.feedback is None
    assert current.feedback_user_id is None

    missing = MagicMock()
    missing.exec.return_value.first.return_value = None
    assert (
        save_visit_record_extract_feedback(
            missing,
            visit_id="rec-1",
            unique_id="gone",
            feedback="up",
            feedback_comment=None,
            user_id="user-9",
        )
        is None
    )
    missing.commit.assert_not_called()


def test_save_decisions_keeps_latest_and_logs_history(caplog):
    session = MagicMock()
    adopted = _StoredItem(
        unique_id="ext-a",
        status="PENDING",
        adopted_todo_id=None,
        reject_reason=None,
    )
    rejected = _StoredItem(
        unique_id="ext-b",
        status="ADOPTED",
        adopted_todo_id="todo-old",
        reject_reason=None,
    )
    session.exec.return_value.all.return_value = [adopted, rejected]
    caplog.set_level(logging.INFO)

    data = save_visit_record_extract_decisions(
        session,
        visit_id="rec-1",
        user_id="user-9",
        items=[
            {"unique_id": "ext-a", "action": "adopt", "adopted_todo_id": "todo-1", "reject_reason": "忽略"},
            {"unique_id": "ext-b", "action": "reject", "adopted_todo_id": "todo-x", "reject_reason": "  不相关  "},
            {"unique_id": "missing", "action": "adopt"},
        ],
    )

    assert data["results"][0] == {
        "unique_id": "ext-a",
        "ok": True,
        "status": "ADOPTED",
        "adopted_todo_id": "todo-1",
        "reject_reason": None,
    }
    assert data["results"][1]["status"] == "REJECTED"
    assert data["results"][1]["adopted_todo_id"] is None
    assert data["results"][1]["reject_reason"] == "不相关"
    assert data["results"][2] == {"unique_id": "missing", "ok": False}
    assert adopted.status == "ADOPTED"
    assert adopted.adopted_todo_id == "todo-1"
    assert adopted.reject_reason is None
    assert rejected.status == "REJECTED"
    assert rejected.adopted_todo_id is None
    assert rejected.reject_reason == "不相关"
    session.commit.assert_called_once()
    assert caplog.text.count("visit_extract_interaction") == 2
    assert "kind=adopt" in caplog.text
    assert "kind=reject" in caplog.text
    assert "todo-old" in caplog.text
    assert "todo-1" in caplog.text


def test_save_decisions_skips_commit_when_nothing_matches():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    data = save_visit_record_extract_decisions(
        session,
        visit_id="rec-1",
        user_id="user-9",
        items=[{"unique_id": "gone", "action": "reject", "reject_reason": "没有这条"}],
    )

    assert data["results"] == [{"unique_id": "gone", "ok": False}]
    session.commit.assert_not_called()
