from typing import List, Optional, Set

from sqlmodel import Session

from app.models.data_source import DataSource, DataSourceType
from app.models.knowledge_base import KnowledgeBaseDataSource

from app.rag.types import CrmDataType

REVIEW_DATASOURCE_NAME_PREFIX = "CRM Review Session"
ALLOWED_REVIEW_DATA_TYPES: Set[str] = {
    CrmDataType.REVIEW_SESSION.value,
    CrmDataType.REVIEW_SNAPSHOT.value,
    CrmDataType.REVIEW_RISK_PROGRESS.value,
}
DEFAULT_REVIEW_DATA_TYPES: List[str] = [
    CrmDataType.REVIEW_SESSION.value,
    CrmDataType.REVIEW_SNAPSHOT.value,
    CrmDataType.REVIEW_RISK_PROGRESS.value,
]
REVIEW_STAGE_ORDER = {
    "initial_edit": 0,
    "first_calculating": 1,
    "first_calc_ready": 2,
    "lead_review": 3,
    "second_calculating": 4,
    "completed": 5,
}


def _short_session_id(session_id: str, length: int = 8) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    return sid[:length]


def normalize_review_data_types(review_data_types: Optional[List[str]]) -> List[str]:
    if not review_data_types:
        return list(DEFAULT_REVIEW_DATA_TYPES)
    normalized = []
    for item in review_data_types:
        v = str(item or "").strip()
        if not v:
            continue
        if v not in ALLOWED_REVIEW_DATA_TYPES:
            raise ValueError(f"Unsupported review_data_type: {v}")
        normalized.append(v)
    deduped = sorted(set(normalized))
    if not deduped:
        return list(DEFAULT_REVIEW_DATA_TYPES)
    return deduped


def validate_review_index_scope_by_stage(stage: str, review_data_types: List[str]) -> None:
    """
    Validate requested review index types by review session stage.

    Rules:
    - REVIEW_RISK_PROGRESS: allowed only at first_calc_ready and later.
    - REVIEW_SESSION: allowed only at first_calc_ready and later.
    - REVIEW_SNAPSHOT: allowed only at first_calc_ready and later.
    """
    stage_key = str(stage or "").strip()
    stage_rank = REVIEW_STAGE_ORDER.get(stage_key, -1)
    min_allowed_rank = REVIEW_STAGE_ORDER["first_calc_ready"]
    violations: List[str] = []
    guarded_types = (
        CrmDataType.REVIEW_RISK_PROGRESS.value,
        CrmDataType.REVIEW_SESSION.value,
        CrmDataType.REVIEW_SNAPSHOT.value,
    )

    for dt in guarded_types:
        if dt in review_data_types and stage_rank < min_allowed_rank:
            violations.append(f"{dt} 仅可在 first_calc_ready 及后续阶段构建")

    if violations:
        raise ValueError(
            f"Review stage={stage_key or 'unknown'} 不满足构建条件: " + "；".join(violations)
        )


def build_review_datasource_name(session_id: str, session_name: Optional[str] = None) -> str:
    sid = str(session_id or "").strip()
    sid_short = _short_session_id(sid)
    sname = str(session_name or "").strip()
    if sname and sid_short:
        # Keep session_id suffix for uniqueness when names collide.
        return f"{REVIEW_DATASOURCE_NAME_PREFIX} ({sname} | {sid_short})"
    if sname:
        return f"{REVIEW_DATASOURCE_NAME_PREFIX} ({sname})"
    if sid_short:
        return f"{REVIEW_DATASOURCE_NAME_PREFIX} ({sid_short})"
    return f"{REVIEW_DATASOURCE_NAME_PREFIX} (unknown)"


def get_or_create_review_datasource_id(
    db_session: Session,
    kb,
    *,
    session_id: str,
    session_name: Optional[str] = None,
) -> int:
    """Find an existing review datasource in the KB, or create one."""
    datasource_name = build_review_datasource_name(
        session_id=session_id,
        session_name=session_name,
    )
    for ds in kb.data_sources:
        if ds.name == datasource_name and not ds.deleted_at:
            return ds.id

    ds = DataSource(
        name=datasource_name,
        description=f"Auto-created datasource for review session indexing ({session_id})",
        data_source_type=DataSourceType.CRM,
        config=[],
    )
    db_session.add(ds)
    db_session.flush()

    link = KnowledgeBaseDataSource(
        knowledge_base_id=kb.id,
        data_source_id=ds.id,
    )
    db_session.add(link)
    db_session.commit()
    db_session.refresh(ds)
    return ds.id
