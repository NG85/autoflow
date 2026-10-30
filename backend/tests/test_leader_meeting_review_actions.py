"""例会复盘抽取：经营事项只赞踩，员工待办还可以采纳和挂任务。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.leader_meeting_review_actions import (
    link_meeting_review_task,
    load_meeting_review_item,
    save_meeting_review_comments,
    save_meeting_review_decisions,
    save_meeting_review_feedback,
)
from app.services.subject_interaction import SubjectInteractionError
from app.services.leader_meeting_review_card import extracts_for_viewer


def _row(item_type, unique_id="item-1", subject_id="uploader-1", **overrides):
    data = {
        "unique_id": unique_id,
        "item_type": item_type,
        "subject_type": "USER",
        "subject_id": subject_id,
        "audience": "sales",
        "status": "PENDING",
        "is_deleted": 0,
        "report_date": date(2026, 10, 28),
        "extract_key": unique_id,
        "source_report_unique_id": "RPT-1",
        "reject_reason": None,
        "adopted_todo_id": None,
        "feedback": None,
        "feedback_comment": None,
        "feedback_user_id": None,
        "feedback_at": None,
        "updated_at": None,
        "payload": {"claim": "事项", "confidence": 0.5},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _session(row):
    session = MagicMock()
    session.exec.return_value.first.return_value = row
    session.exec.return_value.all.return_value = [row] if row is not None else []
    return session


def test_feedback_updates_an_event_and_leaves_the_decision_alone():
    row = _row("MANAGEMENT_EVENT", status="PENDING")
    saved = save_meeting_review_feedback(
        _session(row),
        row,
        user_id="user-1",
        feedback="down",
        feedback_comment="依据不足",
    )
    assert saved["feedback"] == "down"
    assert saved["feedback_comment"] == "依据不足"
    assert row.status == "PENDING"


def test_event_cannot_be_adopted_and_todo_can():
    event = _row("MANAGEMENT_EVENT", unique_id="event-1")
    todo = _row("EMPLOYEE_TODO_CANDIDATE", unique_id="todo-1")
    session = MagicMock()
    session.exec.return_value.all.return_value = [event, todo]
    result = save_meeting_review_decisions(
        session,
        "uploader-1",
        [
            {"unique_id": "event-1", "action": "adopt"},
            {"unique_id": "todo-1", "action": "reject", "reject_reason": "先不做"},
        ],
    )
    assert result["results"][0] == {"unique_id": "event-1", "ok": False}
    assert result["results"][1]["ok"] is True
    assert result["results"][1]["status"] == "REJECTED"
    assert todo.reject_reason == "先不做"
    assert event.status == "PENDING"


def test_reporting_leader_can_view_and_reject(monkeypatch):
    todo = _row("EMPLOYEE_TODO_CANDIDATE", subject_id="uploader-1")
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.reporting_leader_tokens",
        lambda session, uploader_oauth_id: {"ou_leader"},
    )
    assert load_meeting_review_item(
        _session(todo),
        "leader-oauth",
        "item-1",
        todo_only=True,
        open_ids=["ou_leader"],
    ) is todo
    stranger = load_meeting_review_item(
        _session(todo),
        "other-oauth",
        "item-1",
        todo_only=True,
        open_ids=["ou_other"],
    )
    assert stranger is None
    result = save_meeting_review_decisions(
        _session(todo),
        "leader-oauth",
        [{"unique_id": "item-1", "action": "reject", "reject_reason": "先不做"}],
        ["ou_leader"],
    )
    assert result["results"][0]["ok"] is True
    assert todo.status == "REJECTED"


def test_task_link_is_only_for_employee_todos(monkeypatch):
    event = _row("MANAGEMENT_EVENT")
    assert load_meeting_review_item(_session(event), "uploader-1", "item-1", todo_only=True) is None
    todo = _row("EMPLOYEE_TODO_CANDIDATE")
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.require_active_todo",
        lambda session, todo_id: todo_id,
    )
    linked = link_meeting_review_task(
        _session(todo),
        todo,
        user_id="user-1",
        todo_id="todo-9",
        adopt=True,
    )
    assert linked["adopted"] is True
    assert linked["status"] == "ADOPTED"
    assert todo.adopted_todo_id == "todo-9"


def test_extracts_include_feedback_and_linked_tasks(monkeypatch):
    row = _row(
        "EMPLOYEE_TODO_CANDIDATE",
        feedback="up",
        payload={
            "claim": "确认流程",
            "confidence": 0.8,
            "assignee_name": "王磊",
            "action": "确认流程",
            "due_text": "本周五",
            "verification_criteria": "拿到名单",
        },
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_report_extract_rows",
        lambda session, report_id: [row],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.load_threads",
        lambda session, subject_type, subject_ids: {subject_ids[0]: {"comments": [], "todo_ids": ["todo-9"]}},
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.load_task_summaries",
        lambda session, todo_ids: {
            "todo-9": {
                "unique_id": "todo-9",
                "title": "确认流程",
                "owner_id": None,
                "owner_name": None,
                "due_date": None,
                "status": "PENDING",
                "ai_status": None,
            }
        },
    )
    data = extracts_for_viewer(MagicMock(), "RPT-1", "uploader-1")
    item = data["items"]["employee_todo_candidates"][0]
    assert item["feedback"] == "up"
    assert item["status"] == "PENDING"
    assert item["tasks"][0]["unique_id"] == "todo-9"
    assert item["comments"] == []
    assert data["can_manage"] is True
    assert "management_events" not in data["items"]


def test_company_manager_can_view_but_not_change(monkeypatch):
    row = _row("EMPLOYEE_TODO_CANDIDATE", subject_id="uploader-1")
    monkeypatch.setattr(
        "app.services.leader_meeting_review_card.load_report_extract_rows",
        lambda session, report_id: [row],
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.reporting_leader_tokens",
        lambda session, uploader_oauth_id: set(),
    )
    monkeypatch.setattr(
        "app.services.leader_meeting_review_actions.load_threads",
        lambda session, subject_type, subject_ids: {},
    )
    data = extracts_for_viewer(
        MagicMock(),
        "RPT-1",
        "exec-oauth",
        company_manager=True,
    )
    assert list(data["items"]) == ["employee_todo_candidates"]
    assert data["can_manage"] is False
    assert load_meeting_review_item(_session(row), "exec-oauth", "item-1") is None


class _CommentStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def exec(self, statement):
        result = MagicMock()
        result.all.return_value = list(self.rows)
        return result

    def add(self, row):
        if row not in self.rows:
            self.rows.append(row)

    def commit(self):
        return None


def test_comments_can_be_added_replied_and_edited_by_the_author():
    store = _CommentStore()
    row = _row("MANAGEMENT_EVENT")
    saved = save_meeting_review_comments(
        store,
        row,
        user_id="user-1",
        author="甲",
        comments=[{"content": "先确认范围"}],
    )
    assert saved[0]["content"] == "先确认范围"
    assert saved[0]["author_id"] == "user-1"
    assert saved[0]["reply_to_id"] is None
    parent_id = saved[0]["id"]
    replied = save_meeting_review_comments(
        store,
        row,
        user_id="user-2",
        author="乙",
        comments=[{"content": "周五前给结论", "reply_to_id": parent_id}],
    )
    assert replied[-1]["reply_to_id"] == parent_id
    assert replied[-1]["author_id"] == "user-2"
    edited = save_meeting_review_comments(
        store,
        row,
        user_id="user-1",
        author="甲",
        comments=[{"id": parent_id, "content": "先确认范围和负责人"}],
    )
    assert edited[0]["content"] == "先确认范围和负责人"
    assert edited[0]["author_id"] == "user-1"
    try:
        save_meeting_review_comments(
            store,
            row,
            user_id="user-2",
            author="乙",
            comments=[{"id": parent_id, "content": "改掉别人的"}],
        )
    except SubjectInteractionError as exc:
        assert "只能编辑本人的评论" in str(exc)
    else:
        raise AssertionError("expected own-comment edit only")
