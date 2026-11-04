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
    append_or_edit_crm_comments,
    comment_message_tail,
    comment_note_lines,
    comment_notify_lines,
    comment_subject_label,
    fill_missing_comment_authors,
    incoming_author_matches_current_user,
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

    merged, appended, edited_items = append_or_edit_crm_comments(
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


def test_blank_author_id_is_stored_as_current_user():
    merged, appended, edited_items = append_or_edit_crm_comments(
        [],
        [{"content": "hi", "type": "comment", "author": "Alice"}],
        "user-1",
    )

    assert edited_items == []
    assert len(appended) == 1
    assert appended[0]["author_id"] == "user-1"
    assert appended[0]["content"] == "hi"
    assert merged[0]["author_id"] == "user-1"


def test_other_author_id_rejects_the_whole_write():
    with pytest.raises(CRMCommentValidationError, match="author_id 与当前登录用户不一致"):
        append_or_edit_crm_comments(
            [],
            [{"author_id": "user-2", "content": "hack", "type": "comment"}],
            "user-1",
        )


def test_comment_subject_label_keeps_present_names_and_clips_long_ones():
    assert comment_subject_label("甲客户", "乙商机") == "甲客户  乙商机"
    assert comment_subject_label("甲客户", None) == "甲客户"
    assert comment_subject_label(None, "") == ""
    assert comment_subject_label("甲客户", None, "乙商机") == "甲客户  乙商机"

    long_account = "客" * 30
    long_opp = "机" * 30
    label = comment_subject_label(long_account, long_opp)
    assert label == f"{'客' * 16}...  {'机' * 16}..."
    assert len(label) == 40
    assert "..." in label.split("  ")[0] and "..." in label.split("  ")[1]

    only_customer = comment_subject_label("客" * 50, "   ")
    assert only_customer == f"{'客' * 37}..."
    assert len(only_customer) == 40

    header, sentence = comment_notify_lines(
        author_name="乙",
        scene="跟进记录",
        entity_name=label,
        recipient_user_id="user-1",
        addressed_user_id="user-1",
        is_reply=False,
    )
    assert header == "新评论"
    assert sentence == f"@乙在跟进记录 {label} 中给你留了评论"


def test_comment_message_tail_quotes_parent_on_every_reply():
    assert comment_message_tail(content="周五前给结论", label="评论") == ["评论：周五前给结论"]
    assert comment_message_tail(
        content="周五前给结论",
        parent_content="先问客户",
        include_parent=True,
        label="回复",
    ) == ["评论：先问客户", "周五前给结论"]


def test_comment_note_lines_stand_alone_without_scene_name():
    assert comment_note_lines(
        author_name="王经理",
        entity_name="甲客户  乙商机",
        matter="特价必须今天拍板",
    ) == (
        "负责人留言",
        "@王经理就「甲客户  乙商机」给你留言",
        "事项：特价必须今天拍板",
    )
    assert comment_note_lines(
        author_name="乙",
        entity_name="特价必须今天拍板",
        matter="特价必须今天拍板",
        suffix="（编辑过）",
    ) == (
        "负责人留言（编辑过）",
        "@乙就「特价必须今天拍板」给你留言",
        "",
    )
    assert comment_note_lines(author_name="", entity_name="", matter="") == (
        "负责人留言",
        "@有人给你留言",
        "",
    )


def test_comment_notify_lines_follow_feishu_comment_wording():
    assert comment_notify_lines(
        author_name="乙",
        scene="跟进记录",
        entity_name="甲客户  乙商机",
        recipient_user_id="user-1",
        addressed_user_id="user-1",
        is_reply=False,
    ) == ("新评论", "@乙在跟进记录 甲客户  乙商机 中给你留了评论")
    assert comment_notify_lines(
        author_name="乙",
        scene="跟进记录",
        entity_name="甲客户",
        recipient_user_id="11111111-1111-1111-1111-111111111111",
        addressed_user_id="11111111111111111111111111111111",
        is_reply=True,
        suffix="（编辑过）",
    ) == ("评论回复（编辑过）", "@乙在跟进记录 甲客户 中回复了你的评论")
    assert comment_notify_lines(
        author_name="乙",
        scene="跟进记录",
        entity_name="",
        recipient_user_id="user-1",
        addressed_user_id="user-1",
        is_reply=False,
    ) == ("新评论", "@乙在跟进记录中给你留了评论")
    assert comment_notify_lines(
        author_name="乙",
        scene="例会复盘",
        entity_name="甲客户 · 关键人",
        recipient_user_id="mentioned-1",
        addressed_user_id="uploader-user",
        is_reply=True,
    ) == ("评论提及", "@乙在例会复盘 甲客户 · 关键人 中提到了你")


def test_incoming_author_id_may_be_blank_but_not_someone_else():
    assert incoming_author_matches_current_user("", "user-1")
    assert incoming_author_matches_current_user(None, "user-1")
    assert incoming_author_matches_current_user("  ", "user-1")
    assert incoming_author_matches_current_user("user-1", "user-1")
    assert not incoming_author_matches_current_user("user-2", "user-1")


def test_allow_edit_rejects_other_users_comment():
    with pytest.raises(CRMCommentValidationError, match="只能编辑本人的评论"):
        append_or_edit_crm_comments(
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
        )


def test_allow_edit_appends_when_id_is_missing_or_unknown():
    merged, appended, edited_items = append_or_edit_crm_comments(
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
    )

    assert len(appended) == 1
    assert appended[0]["content"] == "brand new"
    assert appended[0]["id"] != "client-tmp"
    assert [item["id"] for item in edited_items] == ["c1"]
    edited = next(item for item in merged if item["id"] == "c1")
    assert edited["content"] == "edited"


def test_append_keeps_existing_order_and_adds_at_the_end():
    merged, appended, edited_items = append_or_edit_crm_comments(
        [
            {
                "id": "later",
                "author_id": "user-2",
                "author": "Bob",
                "content": "second stored",
                "type": "comment",
                "created_at": "2026-10-02T10:00:00+08:00",
            },
            {
                "id": "earlier",
                "author_id": "user-1",
                "author": "Alice",
                "content": "first stored",
                "type": "comment",
                "created_at": "2026-10-01T10:00:00+08:00",
            },
        ],
        [{"author_id": "user-1", "author": "Alice", "content": "new", "type": "comment"}],
        "user-1",
    )

    assert edited_items == []
    assert [item["id"] for item in merged[:2]] == ["later", "earlier"]
    assert merged[-1]["content"] == "new"
    assert appended[0]["id"] == merged[-1]["id"]


def test_allow_edit_updates_own_task():
    merged, appended, edited_items = append_or_edit_crm_comments(
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


def test_fill_missing_comment_authors_from_profile(monkeypatch):
    uid = "11111111-1111-1111-1111-111111111111"
    seen: dict[str, list[str]] = {}

    def fake_names(session, user_ids):
        seen["ids"] = list(user_ids)
        return {uid: "张三"}

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_names_by_user_ids",
        fake_names,
    )
    comments = [
        {"author_id": uid, "author": "", "content": "a"},
        {"author_id": uid.replace("-", ""), "author": None, "content": "b"},
        {"author_id": uid, "author": "已有", "content": "c"},
        {"author_id": "", "author": "", "content": "d"},
        CRMComment(author_id=uid, author="  ", content="e"),
    ]

    fill_missing_comment_authors(object(), comments)

    assert comments[0]["author"] == "张三"
    assert comments[1]["author"] == "张三"
    assert comments[2]["author"] == "已有"
    assert comments[3]["author"] == ""
    assert comments[4].author == "张三"
    assert seen["ids"] == [uid, uid.replace("-", ""), uid]


def test_fill_missing_comment_authors_keeps_blank_when_lookup_fails(monkeypatch):
    def boom(session, user_ids):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_names_by_user_ids",
        boom,
    )
    comments = [{"author_id": "11111111-1111-1111-1111-111111111111", "author": ""}]

    fill_missing_comment_authors(object(), comments)

    assert comments[0]["author"] == ""


def test_fill_missing_comment_authors_skips_lookup_when_author_present(monkeypatch):
    def boom(session, user_ids):
        raise AssertionError("should not look up")

    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_names_by_user_ids",
        boom,
    )
    comments = [{"author_id": "user-1", "author": "甲"}]

    fill_missing_comment_authors(object(), comments)

    assert comments[0]["author"] == "甲"


def test_today_highlight_comment_payload_accepts_recipient_user_ids():
    payload = InterventionCommentsUpdate(
        comments=[InterventionCommentIn(content="先问客户")],
        recipient_user_ids=["user-2", "user-2", ""],
    )
    assert payload.recipient_user_ids == ["user-2", "user-2", ""]
