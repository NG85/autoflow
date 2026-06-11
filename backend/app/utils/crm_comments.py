"""CRM JSON 评论列表的合并与校验（拜访记录、周跟进等复用）。"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
from zoneinfo import ZoneInfo


class CRMCommentValidationError(ValueError):
    """评论 payload 校验失败。"""


def ensure_comment_id(item: Dict[str, Any]) -> Dict[str, Any]:
    """为历史 comment 懒补 id；历史 task 无 id 时保持原样（不写入）。"""
    out = dict(item)
    if str(out.get("id") or "").strip():
        return out
    item_type = str(out.get("type") or "comment").strip().lower()
    if item_type != "task":
        out["id"] = str(uuid4())
    return out


def merge_append_crm_comments(
    existing_raw: object,
    new_comments: Optional[List[Dict[str, Any]]],
    current_user_id: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    在既有评论后追加当前用户的新评论；为历史 comment 懒补 id（历史 task 无 id 时不填充）。
    返回 (merged_sorted, appended)。
    """
    current_user_id_str = str(current_user_id or "")
    now_bj = now or datetime.now(ZoneInfo("Asia/Shanghai"))

    existing_list = existing_raw if isinstance(existing_raw, list) else []
    kept_others: List[Dict[str, Any]] = []
    existing_my: List[Dict[str, Any]] = []
    for item in existing_list:
        if not isinstance(item, dict):
            continue
        normalized = ensure_comment_id(item)
        if str(normalized.get("author_id") or "") != current_user_id_str:
            kept_others.append(normalized)
        else:
            existing_my.append(normalized)

    comment_by_id: Dict[str, Dict[str, Any]] = {}
    for item in kept_others + existing_my:
        cid = str(item.get("id") or "").strip()
        if cid:
            comment_by_id[cid] = item

    appended: List[Dict[str, Any]] = []
    for c in (new_comments or []):
        if not isinstance(c, dict):
            continue
        if str(c.get("author_id") or "") != current_user_id_str:
            continue
        reply_to_id = str(c.get("reply_to_id") or "").strip()
        if reply_to_id:
            parent = comment_by_id.get(reply_to_id)
            if parent is None:
                raise CRMCommentValidationError(f"reply_to_id 无效：未找到 id={reply_to_id} 的评论")
            parent_type = str(parent.get("type") or "comment").strip().lower()
            if parent_type != "comment":
                raise CRMCommentValidationError("仅支持回复 type=comment 的评论，不能回复任务条目")
        item_type = str(c.get("type") or "comment").strip().lower()
        if item_type == "task":
            new_id = str(c.get("id") or "").strip()
            if new_id and new_id in comment_by_id:
                raise CRMCommentValidationError(f"任务 id 已存在：id={new_id}")
        else:
            new_id = str(uuid4())

        created_at = c.get("created_at") or now_bj
        if isinstance(created_at, datetime):
            created_at_str = created_at.isoformat()
        else:
            created_at_str = str(created_at)
        new_comment: Dict[str, Any] = {
            "author_id": current_user_id_str,
            "author": c.get("author"),
            "content": c.get("content"),
            "type": c.get("type"),
            "created_at": created_at_str,
        }
        if new_id:
            new_comment["id"] = new_id
        if reply_to_id:
            new_comment["reply_to_id"] = reply_to_id
        appended.append(new_comment)
        if new_id:
            comment_by_id[new_id] = new_comment

    merged: List[Dict[str, Any]] = kept_others + existing_my + appended

    def _sort_key(x: Dict[str, Any]) -> tuple[int, str]:
        v = str(x.get("created_at") or "")
        try:
            return (0, datetime.fromisoformat(v).isoformat())
        except Exception:
            return (1, v)

    merged.sort(key=_sort_key)
    return merged, appended
