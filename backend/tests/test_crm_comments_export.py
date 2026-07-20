from app.utils.crm_comments import format_crm_comments_for_export


def test_format_crm_comments_for_export_filters_and_sorts_by_created_at():
    comments = [
        {
            "author": "Bob",
            "content": "later comment",
            "type": "comment",
            "created_at": "2026-01-02T02:00:00Z",
        },
        {
            "author": "Alice",
            "content": "earlier comment",
            "type": "comment",
            "created_at": "2026-01-01T02:00:00Z",
        },
        {
            "author": "Carol",
            "content": "a task",
            "type": "task",
            "created_at": "2026-01-03T02:00:00+00:00",
        },
    ]

    assert format_crm_comments_for_export(comments, comment_type="comment") == (
        "Alice（2026-01-01 10:00:00）：earlier comment\n"
        "Bob（2026-01-02 10:00:00）：later comment"
    )
    assert format_crm_comments_for_export(comments, comment_type="task") == (
        "Carol（2026-01-03 10:00:00）：a task"
    )


def test_format_crm_comments_for_export_defaults_missing_type_to_comment():
    comments = [{"author": "Alice", "content": "hello", "created_at": "2026-01-01T10:00:00+08:00"}]

    assert format_crm_comments_for_export(comments, comment_type="comment") == (
        "Alice（2026-01-01 10:00:00）：hello"
    )
    assert format_crm_comments_for_export(comments, comment_type="task") == ""
