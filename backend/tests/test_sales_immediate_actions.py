"""一线销售只处理自己的立即处理：赞踩、采纳，以及别人的行和风险行不可写。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.leader_intervention_actions import save_feedback
from app.services.sales_immediate_actions import (
    load_own_highlight_item,
    save_sales_highlight_decisions,
)


def _item(**overrides):
    data = {
        "unique_id": "item-1",
        "subject_type": "USER",
        "subject_id": "oauth-1",
        "audience": "sales",
        "item_type": "IMMEDIATE_ACTION",
        "status": "PENDING",
        "is_deleted": 0,
        "payload": {"claim": "确认排期"},
        "reject_reason": None,
        "adopted_todo_id": None,
        "feedback": None,
        "feedback_comment": None,
        "feedback_user_id": None,
        "feedback_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _session_with(row):
    session = MagicMock()
    session.exec.return_value.first.return_value = row
    session.exec.return_value.all.return_value = [row] if row is not None else []
    return session


def test_feedback_updates_own_immediate_item():
    row = _item(status="ADOPTED", adopted_todo_id="todo-kept")
    session = _session_with(row)
    found = load_own_highlight_item(session, "oauth-1", "item-1")
    assert found is row
    saved = save_feedback(
        session,
        found,
        user_id="user-9",
        feedback="up",
        feedback_comment="有用",
    )
    assert saved["feedback"] == "up"
    assert row.status == "ADOPTED"
    assert row.adopted_todo_id == "todo-kept"
    assert row.payload == {"claim": "确认排期"}


def test_other_person_and_superseded_are_not_writable():
    session = _session_with(_item(subject_id="oauth-other"))
    assert load_own_highlight_item(session, "oauth-1", "item-1") is None
    session = _session_with(_item(subject_type="DEPARTMENT", audience="leader"))
    assert load_own_highlight_item(session, "oauth-1", "item-1") is None
    session = _session_with(_item(status="SUPERSEDED"))
    assert load_own_highlight_item(session, "oauth-1", "item-1") is None


def test_risk_and_next_week_can_be_adopted():
    for item_type in ("OPEN_RISK", "NEXT_WEEK_ACTION_CANDIDATE"):
        row = _item(item_type=item_type)
        session = _session_with(row)
        assert load_own_highlight_item(session, "oauth-1", "item-1") is row
        result = save_sales_highlight_decisions(
            session,
            "oauth-1",
            "user-9",
            [{"unique_id": "item-1", "action": "adopt"}],
        )
        assert result["results"][0]["ok"] is True
        assert row.status == "ADOPTED"


def test_adopt_and_reject_keep_feedback():
    row = _item(feedback="up", feedback_comment="有用")
    session = _session_with(row)
    adopted = save_sales_highlight_decisions(
        session,
        "oauth-1",
        "user-9",
        [{"unique_id": "item-1", "action": "adopt", "adopted_todo_id": "todo-1"}],
    )
    assert adopted["results"][0]["ok"] is True
    assert adopted["results"][0]["status"] == "ADOPTED"
    assert row.feedback == "up"
    rejected = save_sales_highlight_decisions(
        session,
        "oauth-1",
        "user-9",
        [{"unique_id": "item-1", "action": "reject", "reject_reason": "今天不做"}],
    )
    assert rejected["results"][0]["status"] == "REJECTED"
    assert row.adopted_todo_id is None
    assert row.feedback == "up"


def test_batch_adopt_rejects_missing_todo():
    row = _item()
    session = _session_with(row)

    def missing_todo(session, todo_id):
        raise ValueError("任务不存在或已删除")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "app.services.sales_immediate_actions.require_active_todo",
        missing_todo,
    )
    try:
        with pytest.raises(ValueError, match="任务不存在或已删除"):
            save_sales_highlight_decisions(
                session,
                "oauth-1",
                "user-9",
                [{"unique_id": "item-1", "action": "adopt", "adopted_todo_id": "missing"}],
            )
    finally:
        monkeypatch.undo()
    assert row.status == "PENDING"
    session.commit.assert_not_called()


def test_someone_elses_item_is_not_adopted():
    row = _item(subject_id="oauth-other")
    session = _session_with(row)
    result = save_sales_highlight_decisions(
        session,
        "oauth-1",
        "user-9",
        [{"unique_id": "item-1", "action": "adopt"}],
    )
    assert result["results"] == [{"unique_id": "item-1", "ok": False}]
    assert row.status == "PENDING"
    session.commit.assert_not_called()
