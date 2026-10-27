from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.routes.crm.models import CRMComment, VisitRecordCommentsUpdate
from app.api.routes.crm.today_highlights.router import (
    InterventionCommentIn,
    InterventionCommentsUpdate,
)
from app.utils.crm_comments import (
    CRMCommentValidationError,
    merge_append_crm_comments,
    resolve_comment_notify_recipients,
)


def test_crm_comment_omits_updated_at_when_empty():
    dumped = CRMComment(content="hi").model_dump(mode="json")
    assert "updated_at" not in dumped


def test_crm_comment_includes_updated_at_when_present():
    updated_at = datetime(2026, 10, 23, 16, 4, tzinfo=ZoneInfo("Asia/Shanghai"))
    dumped = CRMComment(content="hi", updated_at=updated_at).model_dump(mode="json")
    assert dumped["updated_at"] == updated_at.isoformat()


def test_allow_edit_updates_own_comment_content_only():
    now = datetime(2026, 10, 23, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    existing = [
        {
            "id": "c1",
            "author_id": "user-1",
            "author": "Alice",
            "content": "old",
            "type": "comment",
            "reply_to_id": "parent",
            "created_at": "2026-10-01T10:00:00+08:00",
        },
        {
            "id": "c2",
            "author_id": "user-2",
            "author": "Bob",
            "content": "other",
            "type": "comment",
            "created_at": "2026-10-02T10:00:00+08:00",
        },
    ]

    merged, appended, edited_items = merge_append_crm_comments(
        existing,
        [
            {
                "id": "c1",
                "author_id": "user-1",
                "author": "Mallory",
                "content": "new",
                "type": "task",
                "reply_to_id": "someone-else",
            }
        ],
        "user-1",
        now=now,
        allow_edit=True,
    )

    assert appended == []
    assert [item["id"] for item in edited_items] == ["c1"]
    edited = next(item for item in merged if item["id"] == "c1")
    assert edited["content"] == "new"
    assert edited["author"] == "Alice"
    assert edited["author_id"] == "user-1"
    assert edited["type"] == "comment"
    assert edited["reply_to_id"] == "parent"
    assert edited["created_at"] == "2026-10-01T10:00:00+08:00"
    assert edited["updated_at"] == now.isoformat()
    other = next(item for item in merged if item["id"] == "c2")
    assert other["content"] == "other"
    assert "updated_at" not in other


def test_allow_edit_rejects_other_users_comment():
    with pytest.raises(CRMCommentValidationError, match="只能编辑本人的评论"):
        merge_append_crm_comments(
            [
                {
                    "id": "c2",
                    "author_id": "user-2",
                    "author": "Bob",
                    "content": "other",
                    "type": "comment",
                    "created_at": "2026-10-02T10:00:00+08:00",
                }
            ],
            [{"id": "c2", "author_id": "user-1", "content": "hack", "type": "comment"}],
            "user-1",
            allow_edit=True,
        )


def test_allow_edit_appends_when_id_is_missing_or_unknown():
    merged, appended, edited_items = merge_append_crm_comments(
        [
            {
                "id": "c1",
                "author_id": "user-1",
                "author": "Alice",
                "content": "old",
                "type": "comment",
                "created_at": "2026-10-01T10:00:00+08:00",
            }
        ],
        [
            {
                "id": "c1",
                "author_id": "user-1",
                "author": "Alice",
                "content": "edited",
                "type": "comment",
            },
            {
                "id": "client-tmp",
                "author_id": "user-1",
                "author": "Alice",
                "content": "brand new",
                "type": "comment",
            },
        ],
        "user-1",
        allow_edit=True,
    )

    assert len(appended) == 1
    assert appended[0]["content"] == "brand new"
    assert appended[0]["id"] != "client-tmp"
    assert [item["id"] for item in edited_items] == ["c1"]
    edited = next(item for item in merged if item["id"] == "c1")
    assert edited["content"] == "edited"


def test_without_allow_edit_existing_comment_id_still_appends():
    merged, appended, edited_items = merge_append_crm_comments(
        [
            {
                "id": "c1",
                "author_id": "user-1",
                "author": "Alice",
                "content": "old",
                "type": "comment",
                "created_at": "2026-10-01T10:00:00+08:00",
            }
        ],
        [{"id": "c1", "author_id": "user-1", "author": "Alice", "content": "new", "type": "comment"}],
        "user-1",
    )

    assert edited_items == []
    assert len(appended) == 1
    assert appended[0]["id"] != "c1"
    assert appended[0]["content"] == "new"
    assert any(item["id"] == "c1" and item["content"] == "old" for item in merged)


def test_allow_edit_updates_own_task():
    merged, appended, edited_items = merge_append_crm_comments(
        [
            {
                "id": "t1",
                "author_id": "user-1",
                "author": "Alice",
                "content": "old task",
                "type": "task",
                "created_at": "2026-10-01T10:00:00+08:00",
            }
        ],
        [{"id": "t1", "author_id": "user-1", "author": "Alice", "content": "new task", "type": "task"}],
        "user-1",
        allow_edit=True,
    )

    assert appended == []
    assert [item["id"] for item in edited_items] == ["t1"]
    assert merged[0]["content"] == "new task"
    assert merged[0]["type"] == "task"
    assert merged[0]["id"] == "t1"


def test_resolve_comment_notify_recipients_uses_default_when_unspecified():
    assert resolve_comment_notify_recipients(
        specified=None,
        default_user_id="recorder-1",
        current_user_id="author-1",
    ) == ["recorder-1"]
    assert resolve_comment_notify_recipients(
        specified=[],
        default_user_id="recorder-1",
        current_user_id="author-1",
    ) == ["recorder-1"]


def test_resolve_comment_notify_recipients_uses_specified_and_skips_self():
    me = "11111111-1111-1111-1111-111111111111"
    other = "22222222-2222-2222-2222-222222222222"
    assert resolve_comment_notify_recipients(
        specified=[me, other, other, "  ", me.replace("-", "")],
        default_user_id="recorder-1",
        current_user_id=me.replace("-", ""),
    ) == [other]


def test_visit_comment_payload_accepts_recipient_user_ids():
    payload = VisitRecordCommentsUpdate(
        comments=[CRMComment(author_id="user-1", content="hi")],
        recipient_user_ids=["user-2", "user-2", ""],
    )
    assert payload.recipient_user_ids == ["user-2", "user-2", ""]


def test_today_highlight_comment_payload_accepts_recipient_user_ids():
    payload = InterventionCommentsUpdate(
        comments=[InterventionCommentIn(content="先问客户")],
        recipient_user_ids=["user-2", "user-2", ""],
    )
    assert payload.recipient_user_ids == ["user-2", "user-2", ""]
