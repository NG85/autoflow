"""CRM JSON 评论的追加、修改与校验（拜访记录、周跟进等复用）。"""

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.utils.date_utils import convert_utc_to_local_timezone

logger = logging.getLogger(__name__)


class CRMCommentValidationError(ValueError):
    """评论 payload 校验失败。"""


def incoming_author_matches_current_user(author_id: object, current_user_id: str) -> bool:
    """空 author_id 视为当前用户。非空则必须与当前登录用户一致。"""
    incoming = str(author_id or "").strip()
    if not incoming:
        return True
    return incoming == str(current_user_id or "").strip()


def ensure_comment_id(item: Dict[str, Any]) -> Dict[str, Any]:
    """为历史 comment 懒补 id；历史 task 无 id 时保持原样（不写入）。"""
    out = dict(item)
    if str(out.get("id") or "").strip():
        return out
    item_type = str(out.get("type") or "comment").strip().lower()
    if item_type != "task":
        out["id"] = str(uuid4())
    return out


def append_or_edit_crm_comments(
    existing_raw: object,
    new_comments: Optional[List[Dict[str, Any]]],
    current_user_id: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    在既有评论后追加，或按 id 修改本人评论的正文。不拆分、不重排已有列表。
    历史 comment 懒补 id；历史 task 无 id 时不补，因此无法被编辑命中。
    id 命中时只写 content 与 updated_at，不改 id、author、reply_to_id、type、created_at。
    命中他人评论，或 author_id 非空且不是当前用户时，抛出 CRMCommentValidationError。
    无 id，或 id 未命中：追加。type=comment 生成新 id；type=task 沿用客户端 id，未传则不生成。
    返回 (comments, appended, edited)。
    """
    current_user_id_str = str(current_user_id or "")
    now_bj = now or datetime.now(ZoneInfo("Asia/Shanghai"))

    existing_list = existing_raw if isinstance(existing_raw, list) else []
    stored: List[Dict[str, Any]] = []
    comment_by_id: Dict[str, Dict[str, Any]] = {}
    for item in existing_list:
        if not isinstance(item, dict):
            continue
        normalized = ensure_comment_id(item)
        stored.append(normalized)
        cid = str(normalized.get("id") or "").strip()
        if cid:
            comment_by_id[cid] = normalized

    appended: List[Dict[str, Any]] = []
    edited: List[Dict[str, Any]] = []
    for c in (new_comments or []):
        if not isinstance(c, dict):
            continue
        if not incoming_author_matches_current_user(c.get("author_id"), current_user_id_str):
            raise CRMCommentValidationError(
                "存在 author_id 与当前登录用户不一致的评论，禁止代他人提交；请仅附加以本人身份发表的评论。"
            )
        incoming_id = str(c.get("id") or "").strip()
        if incoming_id and incoming_id in comment_by_id:
            existing = comment_by_id[incoming_id]
            if str(existing.get("author_id") or "").strip() != current_user_id_str:
                raise CRMCommentValidationError("只能编辑本人的评论")
            existing["content"] = c.get("content")
            existing["updated_at"] = now_bj.isoformat()
            edited.append(existing)
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
        stored.append(new_comment)
        appended.append(new_comment)
        if new_id:
            comment_by_id[new_id] = new_comment

    return stored, appended, edited


def _user_id_key(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return UUID(value).hex
    except Exception:
        return value


def resolve_comment_notify_recipients(
    *,
    specified: Optional[Sequence[Any]] = None,
    default_user_id: str,
    current_user_id: str,
) -> List[str]:
    """指定接收人时用名单；否则用默认规则。空名单视为未指定。跳过自己和重复 id。"""
    picked: List[str] = []
    for item in specified or []:
        uid = str(item or "").strip()
        if uid:
            picked.append(uid)
    candidates: Iterable[str] = picked if picked else [str(default_user_id or "").strip()]
    current_key = _user_id_key(current_user_id)
    result: List[str] = []
    seen: set[str] = set()
    for uid in candidates:
        uid = str(uid or "").strip()
        key = _user_id_key(uid)
        if not key or key == current_key or key in seen:
            continue
        seen.add(key)
        result.append(uid)
    return result


_SUBJECT_LABEL_LIMIT = 40


def _clip_label(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def comment_subject_label(*parts: object, separator: str = "  ", limit: int = _SUBJECT_LABEL_LIMIT) -> str:
    """拼客户名、商机名。缺的部分不留空位；有多段时每段都露出来，过长则截断。"""
    names = [str(part or "").strip() for part in parts]
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return _clip_label(names[0], limit)
    sep_len = len(separator) * (len(names) - 1)
    each = max((limit - sep_len) // len(names), 8)
    clipped = [_clip_label(name, each) for name in names]
    return _clip_label(separator.join(clipped), limit)


_NOTE_MATTER_LIMIT = 80


def comment_note_lines(
    *,
    author_name: str,
    entity_name: str,
    matter: str = "",
    suffix: str = "",
) -> Tuple[str, str, str]:
    """进不了页面的收件人。不写内部场景名，事项和评论都在这条消息里。"""
    author = str(author_name or "").strip() or "有人"
    name = comment_subject_label(entity_name)
    header = f"负责人留言{suffix}" if suffix else "负责人留言"
    sentence = f"@{author}就「{name}」给你留言" if name else f"@{author}给你留言"
    matter_text = _clip_label(str(matter or "").strip(), _NOTE_MATTER_LIMIT)
    matter_line = f"事项：{matter_text}" if matter_text and matter_text != name else ""
    return header, sentence, matter_line


def _comment_preview_text(content: object, limit: int = 200) -> str:
    text = " ".join(str(content or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def comment_message_tail(
    *,
    content: object,
    parent_content: object = "",
    include_parent: bool = False,
    label: str = "评论",
) -> List[str]:
    """回复时先引用被回复原文，新写的话放在引用下面。顶层评论只引用这次新写的话。"""
    new_text = _comment_preview_text(content) or "--"
    if include_parent:
        parent = _comment_preview_text(parent_content) or "--"
        return [f"评论：{parent}", new_text]
    marker = str(label or "").strip() or "评论"
    return [f"{marker}：{new_text}"]


def comment_notify_lines(
    *,
    author_name: str,
    scene: str,
    entity_name: str,
    recipient_user_id: str,
    addressed_user_id: str,
    is_reply: bool,
    suffix: str = "",
) -> Tuple[str, str]:
    """返回卡片短标题和正文首句。首句写成「@谁在场景 对象 中…」，对齐飞书文档评论通知。"""
    author = str(author_name or "").strip() or "有人"
    place = str(scene or "").strip() or "评论"
    name = comment_subject_label(entity_name)
    where = f"{place} {name} 中" if name and name != place else f"{place}中"
    addressed = _user_id_key(addressed_user_id)
    direct = bool(addressed) and addressed == _user_id_key(recipient_user_id)
    if direct and is_reply:
        header = "评论回复"
        sentence = f"@{author}在{where}回复了你的评论"
    elif direct:
        header = "新评论"
        sentence = f"@{author}在{where}给你留了评论"
    else:
        header = "评论提及"
        sentence = f"@{author}在{where}提到了你"
    if suffix:
        header = f"{header}{suffix}"
    return header, sentence


def _comment_field(item: Any, key: str) -> str:
    if isinstance(item, Mapping):
        return str(item.get(key) or "").strip()
    return str(getattr(item, key, "") or "").strip()


def _profile_name(names: Mapping[str, str], author_id: str) -> str:
    raw = str(author_id or "").strip()
    if not raw:
        return ""
    found = str(names.get(raw) or "").strip()
    if found:
        return found
    try:
        parsed = UUID(raw)
    except (ValueError, TypeError):
        return ""
    return str(names.get(str(parsed)) or names.get(parsed.hex) or "").strip()


def fill_missing_comment_authors(session: Any, comments: Sequence[Any]) -> None:
    """author 为空且 author_id 有值时，用档案姓名补显示名。只改本次返回，不写库。"""
    missing: List[Any] = []
    author_ids: List[str] = []
    for item in comments or []:
        if _comment_field(item, "author"):
            continue
        author_id = _comment_field(item, "author_id")
        if not author_id:
            continue
        missing.append(item)
        author_ids.append(author_id)
    if not missing:
        return
    try:
        from app.repositories.user_profile import user_profile_repo

        names = user_profile_repo.get_names_by_user_ids(session, author_ids)
    except Exception as exc:
        logger.warning("补齐评论作者姓名失败: %s", exc)
        return
    for item in missing:
        name = _profile_name(names, _comment_field(item, "author_id"))
        if not name:
            continue
        if isinstance(item, Mapping):
            item["author"] = name
        else:
            item.author = name


def has_nonempty_comments(comments_raw: object) -> bool:
    """判断 comments 数组是否非空（不区分 comment / task）。"""
    return isinstance(comments_raw, list) and len(comments_raw) > 0


def _comment_created_at_sort_key(item: Dict[str, Any]) -> tuple[int, str]:
    v = str(item.get("created_at") or "")
    try:
        return (0, datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat())
    except Exception:
        return (1, v)


def _format_comment_created_at(value: object) -> str:
    if not value:
        return ""
    formatted = convert_utc_to_local_timezone(value)
    return "" if formatted == "--" else formatted


def format_crm_comments_for_export(
    comments_raw: object,
    *,
    comment_type: str,
) -> str:
    """
    按 type 筛选评论/任务，按 created_at 升序拼接为多行文本：
    author（created_at）：content
    """
    if not isinstance(comments_raw, list):
        return ""

    target_type = str(comment_type or "").strip().lower()
    matched: List[Dict[str, Any]] = []
    for item in comments_raw:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "comment").strip().lower()
        if item_type != target_type:
            continue
        matched.append(item)

    matched.sort(key=_comment_created_at_sort_key)

    lines: List[str] = []
    for item in matched:
        author = str(item.get("author") or "")
        created_at = _format_comment_created_at(item.get("created_at"))
        content = str(item.get("content") or "")
        lines.append(f"{author}（{created_at}）：{content}")

    return "\n".join(lines)
