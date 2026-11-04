"""负责人处理今日需介入：权限、改口、撤销，以及评论和任务互不串。"""

from collections import defaultdict
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.crm_subject_interaction import CRMSubjectInteraction
from app.services.department_today_highlights import highlights_for_leader
from app.services.leader_intervention_actions import (
    link_item_task,
    load_actionable_item,
    notify_item_comments,
    save_decisions,
    save_feedback,
    save_item_comments,
)
from app.services.subject_interaction import (
    SUBJECT_DEPARTMENT_INTERVENTION,
    SUBJECT_SKILL_EXTRACT_ITEM,
    SubjectInteractionError,
    append_comments,
    append_todo_id,
    intervention_anchor,
)

DAY = date(2026, 10, 13)


def _item(**overrides):
    payload = {
        "claim": "特价必须今天拍板",
        "leader_action": "今天找张三确认",
        "related_user_id": "oauth-zhang",
        "account_name": "甲客户",
        "opportunity_name": "乙商机",
    }
    payload.update(overrides.pop("payload", {}))
    data = {
        "unique_id": "item-1",
        "subject_type": "USER",
        "subject_id": "oauth-leader",
        "audience": "leader",
        "item_type": "TODAY_INTERVENTION",
        "status": "PENDING",
        "is_deleted": 0,
        "report_date": DAY,
        "extract_key": "key-1",
        "account_id": "acc-1",
        "opportunity_id": "opp-1",
        "reject_reason": None,
        "adopted_todo_id": None,
        "feedback": None,
        "feedback_comment": None,
        "feedback_user_id": None,
        "feedback_at": None,
        "payload": payload,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _led(monkeypatch, oauth_id="oauth-leader"):
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.leader_oauth_id",
        lambda session, user_id: oauth_id,
    )


def _session_with(row):
    session = MagicMock()
    session.exec.return_value.first.return_value = row
    session.exec.return_value.all.return_value = [row] if row is not None else []
    return session


def test_leader_list_returns_extract_state_and_side_records(monkeypatch):
    row = {
        "unique_id": "item-1",
        "subject_type": "USER",
        "subject_id": "oauth-leader",
        "audience": "leader",
        "status": "ADOPTED",
        "is_deleted": 0,
        "item_type": "TODAY_INTERVENTION",
        "extract_key": "item-1",
        "report_date": DAY,
        "account_id": "acc-1",
        "opportunity_id": "opp-1",
        "card_links": [],
        "reject_reason": None,
        "adopted_todo_id": "todo-1",
        "feedback": "up",
        "feedback_comment": "判断准确",
        "feedback_at": datetime(2026, 10, 13, 9, 0, 0),
        "payload": {
            "claim": "特价必须今天拍板",
            "leader_action": "今天确认",
            "related_user_id": "oauth-zhang",
        },
    }
    monkeypatch.setattr(
        "app.services.department_today_highlights.leader_oauth_id",
        lambda session, user_id: "oauth-leader",
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda session, report_date, oauth_user_ids=None: [row],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda session, rows: {"accounts": {}, "opportunities": {}, "people": {}},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_sections",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.repositories.department_mirror.department_mirror_repo.get_department_names_by_ids",
        lambda session, ids: {"dept-mine": "华北销售"},
    )
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.load_threads",
        lambda session, subject_type, subject_ids: {
            "2026-10-13|oauth-leader|item-1": {
                "comments": [{"id": "c1", "content": "先问客户"}],
                "todo_ids": ["todo-9"],
            }
        },
    )
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.load_task_summaries",
        lambda session, todo_ids: {
            "todo-9": {
                "unique_id": "todo-9",
                "title": "确认折扣",
                "owner_id": "oauth-zhang",
                "owner_name": "张三",
                "due_date": "2026-10-13",
                "ai_status": "PENDING",
            }
        },
    )

    data = highlights_for_leader(MagicMock(), "leader-1", DAY)
    item = data["items"]["leader_intervene"][0]
    assert item["status"] == "ADOPTED"
    assert item["adopted_todo_id"] == "todo-1"
    assert item["feedback"] == "up"
    assert item["feedback_comment"] == "判断准确"
    assert item["feedback_at"] == "2026-10-13T09:00:00"
    assert item["reject_reason"] is None
    assert item["comments"] == [{"id": "c1", "content": "先问客户"}]
    assert item["tasks"][0]["title"] == "确认折扣"
    assert item["claim"] == "特价必须今天拍板"


def test_non_leader_and_superseded_are_not_writable(monkeypatch):
    row = _item()
    session = _session_with(row)
    _led(monkeypatch, "")
    assert load_actionable_item(session, "outsider", "item-1") is None

    _led(monkeypatch, "oauth-other")
    assert load_actionable_item(session, "other-leader", "item-1") is None

    superseded = _item(status="SUPERSEDED")
    _led(monkeypatch)
    assert load_actionable_item(_session_with(superseded), "leader-1", "item-1") is None
    deleted = _item(is_deleted=1)
    assert load_actionable_item(_session_with(deleted), "leader-1", "item-1") is None

    decisions = save_decisions(
        _session_with(superseded),
        "leader-1",
        [{"unique_id": "item-1", "action": "adopt"}],
    )
    assert decisions["results"] == [{"unique_id": "item-1", "ok": False}]


def test_feedback_can_be_revoked_without_touching_decision_or_payload():
    row = _item(status="ADOPTED", adopted_todo_id="todo-kept", payload={"claim": "原文"})
    session = _session_with(row)
    saved = save_feedback(
        session,
        row,
        user_id="user-9",
        feedback="down",
        feedback_comment="  不太准  ",
    )
    assert saved["feedback"] == "down"
    assert saved["feedback_comment"] == "不太准"
    assert set(saved) == {"unique_id", "feedback", "feedback_comment", "feedback_at"}
    assert row.feedback_user_id == "user-9"
    assert row.status == "ADOPTED"
    assert row.adopted_todo_id == "todo-kept"
    assert row.payload["claim"] == "原文"

    cleared = save_feedback(
        session,
        row,
        user_id="user-9",
        feedback=None,
        feedback_comment="忽略",
    )
    assert cleared["feedback"] is None
    assert cleared["feedback_comment"] is None
    assert cleared["feedback_at"] is None
    assert row.feedback_user_id is None
    assert row.status == "ADOPTED"


def test_decision_change_of_mind_keeps_feedback(monkeypatch):
    row = _item(feedback="up", feedback_comment="有用", feedback_user_id="user-9")
    session = _session_with(row)
    _led(monkeypatch)

    adopted = save_decisions(
        session,
        "leader-1",
        [{"unique_id": "item-1", "action": "adopt", "adopted_todo_id": "todo-1", "reject_reason": "忽略"}],
    )
    assert adopted["results"][0]["ok"] is True
    assert adopted["results"][0]["status"] == "ADOPTED"
    assert row.reject_reason is None
    assert row.feedback == "up"

    rejected = save_decisions(
        session,
        "leader-1",
        [{"unique_id": "item-1", "action": "reject", "reject_reason": "  先不跟  ", "adopted_todo_id": "todo-1"}],
    )
    assert rejected["results"][0]["status"] == "REJECTED"
    assert rejected["results"][0]["adopted_todo_id"] is None
    assert row.reject_reason == "先不跟"
    assert row.adopted_todo_id is None
    assert row.feedback == "up"
    assert row.feedback_comment == "有用"
    session.commit.assert_called()


def _memory_threads(monkeypatch):
    stored = defaultdict(list)

    def rows_for(session, subject_type, subject_id):
        return list(stored[(subject_type, subject_id)])

    def add(row):
        if isinstance(row, CRMSubjectInteraction):
            stored[(row.subject_type, row.subject_id)].append(row)

    monkeypatch.setattr("app.services.subject_interaction._rows_for_subject", rows_for)
    session = MagicMock()
    session.add.side_effect = add
    return session, stored


def test_comments_stay_on_their_own_subject_and_support_reply_threads(monkeypatch):
    session, stored = _memory_threads(monkeypatch)
    first = append_comments(
        session,
        subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
        subject_id="item-1",
        scene="department_today_highlights",
        actor_user_id="user-9",
        author="王经理",
        comments=[{"content": "先问客户"}],
    )
    root_id = first[0]["id"]
    reply = append_comments(
        session,
        subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
        subject_id="item-1",
        scene="department_today_highlights",
        actor_user_id="user-9",
        author="王经理",
        comments=[{"content": "补充一下", "reply_to_id": root_id}],
    )
    reply_id = reply[1]["id"]
    nested = append_comments(
        session,
        subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
        subject_id="item-1",
        scene="department_today_highlights",
        actor_user_id="user-8",
        author="李经理",
        comments=[{"content": "我来跟", "reply_to_id": reply_id}],
    )
    other = append_comments(
        session,
        subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
        subject_id="item-2",
        scene="department_today_highlights",
        actor_user_id="user-9",
        author="王经理",
        comments=[{"content": "另一条介入"}],
    )

    assert [item["content"] for item in nested] == ["先问客户", "补充一下", "我来跟"]
    assert nested[1]["reply_to_id"] == root_id
    assert nested[2]["reply_to_id"] == reply_id
    assert nested[2]["author_id"] == "user-8"
    assert [item["content"] for item in other] == ["另一条介入"]
    item_1 = stored[(SUBJECT_SKILL_EXTRACT_ITEM, "item-1")]
    item_2 = stored[(SUBJECT_SKILL_EXTRACT_ITEM, "item-2")]
    assert [row.kind for row in item_1] == ["comment", "comment", "comment"]
    assert [row.content for row in item_1] == ["先问客户", "补充一下", "我来跟"]
    assert [row.author_id for row in item_1] == ["user-9", "user-9", "user-8"]
    assert item_2[0].content == "另一条介入"
    assert item_2[0].kind == "comment"

    with pytest.raises(SubjectInteractionError):
        append_comments(
            session,
            subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
            subject_id="item-1",
            scene="department_today_highlights",
            actor_user_id="user-9",
            author="王经理",
            comments=[{"content": "挂错楼", "reply_to_id": "missing"}],
        )
    assert len(stored[(SUBJECT_SKILL_EXTRACT_ITEM, "item-1")]) == 3


def test_linked_task_is_separate_from_comments_and_can_adopt(monkeypatch):
    row = _item(feedback="down")
    original_payload = dict(row.payload)
    anchor = intervention_anchor(row.report_date, row.subject_id, row.extract_key, row.unique_id)
    session, stored = _memory_threads(monkeypatch)
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.require_active_todo",
        lambda session, todo_id: todo_id,
    )
    append_comments(
        session,
        subject_type=SUBJECT_DEPARTMENT_INTERVENTION,
        subject_id=anchor,
        scene="department_today_highlights",
        actor_user_id="user-9",
        author="王经理",
        comments=[{"content": "先评论"}],
    )

    linked = link_item_task(
        session,
        row,
        user_id="user-9",
        todo_id="todo-from-crm",
        adopt=True,
    )

    assert not any("INSERT INTO crm_todos" in str(call.args[0]) for call in session.exec.call_args_list)
    rows = stored[(SUBJECT_DEPARTMENT_INTERVENTION, anchor)]
    assert [item.content for item in rows if item.kind == "comment"] == ["先评论"]
    assert [item.todo_id for item in rows if item.kind == "task"] == ["todo-from-crm"]
    assert next(item.author_id for item in rows if item.kind == "task") == "user-9"
    assert row.status == "ADOPTED"
    assert row.adopted_todo_id == "todo-from-crm"
    assert row.reject_reason is None
    assert row.feedback == "down"
    assert row.payload == original_payload
    assert linked["todo_id"] == "todo-from-crm"
    assert linked["adopted"] is True


def _patch_comment_notify(monkeypatch):
    monkeypatch.setattr(
        "app.services.leader_intervention_actions.load_display_names",
        lambda *_args, **_kwargs: {
            "accounts": {"acc-1": "甲客户"},
            "opportunities": {"opp-1": "乙商机"},
            "people": {},
        },
    )


def test_notify_item_comments_sends_to_specified_recipients(monkeypatch):
    row = _item()
    session = MagicMock()
    sent = []
    _patch_comment_notify(monkeypatch)

    class _Service:
        def send_today_highlight_comment_notification(self, db, *, recipient_user_id, message_text):
            sent.append((recipient_user_id, message_text))
            return {"success": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service",
        _Service(),
    )
    notify_item_comments(
        session,
        row,
        current_user_id="leader-1",
        author="王经理",
        comments=[{"content": "先问客户周五前能不能定"}],
        saved_comments=[{"id": "c1", "author_id": "leader-1", "content": "先问客户周五前能不能定"}],
        recipient_user_ids=["user-2", "leader-1"],
    )

    assert [item[0] for item in sent] == ["user-2"]
    text = sent[0][1]
    assert text.startswith("负责人留言\n@王经理就「甲客户  乙商机」给你留言\n")
    assert "事项：特价必须今天拍板" in text
    assert "今日需介入" not in text
    assert "评论：先问客户周五前能不能定" in text
    assert "http" not in text
    assert "[" not in text


def test_notify_item_comments_reply_defaults_to_parent_author(monkeypatch):
    row = _item()
    session = MagicMock()
    sent = []
    _patch_comment_notify(monkeypatch)

    class _Service:
        def send_today_highlight_comment_notification(self, db, *, recipient_user_id, message_text):
            sent.append((recipient_user_id, message_text))
            return {"success": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service",
        _Service(),
    )
    notify_item_comments(
        session,
        row,
        current_user_id="leader-1",
        author="李经理",
        comments=[{"content": "我来跟", "reply_to_id": "c1"}],
        saved_comments=[
            {"id": "c1", "author_id": "user-9", "content": "先问客户"},
            {"id": "c2", "author_id": "leader-1", "content": "我来跟", "reply_to_id": "c1"},
        ],
    )

    assert [item[0] for item in sent] == ["user-9"]
    assert sent[0][1].startswith("负责人留言\n@李经理就「甲客户  乙商机」给你留言\n")
    assert "事项：特价必须今天拍板" in sent[0][1]
    assert "今日需介入" not in sent[0][1]
    assert "评论：先问客户\n我来跟" in sent[0][1]
    assert "http" not in sent[0][1]


def test_notify_item_comments_skips_top_level_without_recipients(monkeypatch):
    row = _item()
    session = MagicMock()
    sent = []
    _patch_comment_notify(monkeypatch)
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service",
        SimpleNamespace(
            send_today_highlight_comment_notification=lambda *args, **kwargs: sent.append(kwargs)
        ),
    )
    notify_item_comments(
        session,
        row,
        current_user_id="leader-1",
        author="王经理",
        comments=[{"content": "只记一笔"}],
        saved_comments=[{"id": "c1", "author_id": "leader-1", "content": "只记一笔"}],
    )
    assert sent == []


def test_notify_item_comments_does_not_raise_when_send_fails(monkeypatch):
    row = _item()
    session = MagicMock()
    _patch_comment_notify(monkeypatch)

    class _Service:
        def send_today_highlight_comment_notification(self, db, *, recipient_user_id, message_text):
            raise RuntimeError("im down")

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service",
        _Service(),
    )
    notify_item_comments(
        session,
        row,
        current_user_id="leader-1",
        author="王经理",
        comments=[{"content": "先问客户"}],
        saved_comments=[{"id": "c1", "author_id": "leader-1", "content": "先问客户"}],
        recipient_user_ids=["user-2"],
    )


def test_comment_api_does_not_change_extract_status(monkeypatch):
    row = _item(status="PENDING")
    session, _stored = _memory_threads(monkeypatch)
    comments = save_item_comments(
        session,
        row,
        user_id="user-9",
        author="王经理",
        comments=[{"content": "只是讨论"}],
    )
    assert comments[0]["content"] == "只是讨论"
    assert row.status == "PENDING"
    assert row.adopted_todo_id is None


def test_append_todo_does_not_write_comments(monkeypatch):
    session, stored = _memory_threads(monkeypatch)
    ids = append_todo_id(
        session,
        subject_type=SUBJECT_SKILL_EXTRACT_ITEM,
        subject_id="item-1",
        scene="department_today_highlights",
        todo_id="todo-1",
    )
    rows = stored[(SUBJECT_SKILL_EXTRACT_ITEM, "item-1")]
    assert ids == ["todo-1"]
    assert [row.kind for row in rows] == ["task"]
    assert rows[0].todo_id == "todo-1"
    assert rows[0].content is None


def test_batch_adopt_rejects_missing_todo(monkeypatch):
    row = _item()
    session = _session_with(row)
    _led(monkeypatch)

    def missing_todo(session, todo_id):
        raise ValueError("任务不存在或已删除")

    monkeypatch.setattr(
        "app.services.leader_intervention_actions.require_active_todo",
        missing_todo,
    )
    with pytest.raises(ValueError, match="任务不存在或已删除"):
        save_decisions(
            session,
            "leader-1",
            [{"unique_id": "item-1", "action": "adopt", "adopted_todo_id": "missing"}],
        )
    assert row.status == "PENDING"
    assert row.adopted_todo_id is None
    session.commit.assert_not_called()


def test_missing_task_does_not_adopt(monkeypatch):
    row = _item()
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with pytest.raises(ValueError, match="任务不存在或已删除"):
        link_item_task(
            session,
            row,
            user_id="user-9",
            todo_id="missing",
            adopt=True,
        )
    assert row.status == "PENDING"
    assert row.adopted_todo_id is None
    session.commit.assert_not_called()
    session.add.assert_not_called()


def test_another_leader_cannot_write_the_row(monkeypatch):
    from app.services.leader_intervention_actions import load_actionable_item

    own = _item()
    _led(monkeypatch, "oauth-other")
    assert load_actionable_item(_session_with(own), "leader-1", "item-1") is None
    _led(monkeypatch, "oauth-leader")
    assert load_actionable_item(_session_with(own), "leader-1", "item-1") is own
