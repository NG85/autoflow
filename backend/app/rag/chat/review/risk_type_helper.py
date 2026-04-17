import logging
from typing import Dict, List, Optional, Tuple

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewRiskCategory

logger = logging.getLogger(__name__)

# Business-risk defaults for MVP preset.
ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT: List[str] = [
    "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK",
    "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT",
]

# Fallback names for business risks when dictionary rows are missing.
BUSINESS_RISK_TYPE_NAME_FALLBACK: Dict[str, str] = {
    "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK": "有高风险commit商机",
    "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT": "commit商机储备不足风险",
    "COMMIT_JUDGMENT_MISMATCH": "判断风险",
    "COMMIT_REDUCTION": "commit减少",
    "UPSIDE_REDUCTION": "upside减少",
}

# Department/company-level "business risks" currently supported.
BUSINESS_RISK_TYPE_CODES: frozenset[str] = frozenset(BUSINESS_RISK_TYPE_NAME_FALLBACK.keys())

# Relation table uses type_name (not type_code) for business-risk opportunity links.
BUSINESS_RISK_RELATION_TYPE_NAME_BY_CODE: Dict[str, str] = {
    "ACHIEVEMENT_GAP_COMMIT_HIGH_RISK": "业绩达成风险",
    "ACHIEVEMENT_GAP_UPSIDE_INSUFFICIENT": "业绩达成风险",
    "COMMIT_JUDGMENT_MISMATCH": "商机判断风险",
    "COMMIT_REDUCTION": "Commit减少",
    "UPSIDE_REDUCTION": "Upside减少",
}


def resolve_requested_risk_type_codes(template_params: Dict) -> List[str]:
    """Normalize requested risk_type_codes from template params; fallback to defaults."""
    params = template_params or {}
    raw = params.get("risk_type_codes")
    if raw is None:
        return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT)
    if raw == []:
        logger.warning("template_params.risk_type_codes is empty; using default achievement risk codes")
        return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT)
    if not isinstance(raw, list):
        logger.warning(
            "template_params.risk_type_codes must be a list, got %s; using defaults",
            type(raw).__name__,
        )
        return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT)

    normalized: List[str] = []
    seen: set[str] = set()
    for code in raw:
        s = str(code).strip() if code is not None else ""
        if not s or s in seen:
            continue
        seen.add(s)
        normalized.append(s)
    if not normalized:
        logger.warning("No valid non-empty achievement risk type_codes provided; using defaults")
        return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT)
    return normalized


def load_risk_type_name_map(
    db_session: Optional[Session],
    risk_type_codes: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Load code->name map from risk category table plus fallback business risk entries."""
    code_filter = [c for c in (risk_type_codes or []) if c]
    name_map: Dict[str, str] = {}
    if db_session is not None:
        try:
            R = CRMReviewRiskCategory
            stmt = select(R.code, R.name_zh)
            if code_filter:
                stmt = stmt.where(R.code.in_(code_filter))
            rows = db_session.exec(stmt).all()
            for code, name_zh in rows:
                if code:
                    name_map[str(code)] = str(name_zh or "").strip()
        except Exception as e:
            logger.warning("Failed to load crm_review_risk_category map: %s", e)
    for code, fallback_name in BUSINESS_RISK_TYPE_NAME_FALLBACK.items():
        if code_filter and code not in code_filter:
            continue
        name_map.setdefault(code, fallback_name)
    return name_map


def validate_risk_type_codes(
    requested_codes: Optional[List[str]],
    risk_universe_map: Dict[str, str],
) -> Tuple[List[str], bool]:
    """Validate requested codes against risk universe. Returns (validated_codes, used_default_fallback)."""
    if not requested_codes:
        return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT), True
    validated: List[str] = []
    seen: set[str] = set()
    for code in requested_codes:
        c = str(code or "").strip()
        if not c or c in seen:
            continue
        if c not in risk_universe_map:
            logger.warning("Ignored unsupported risk type_code outside risk universe: %s", c)
            continue
        seen.add(c)
        validated.append(c)
    if validated:
        return validated, False
    logger.warning("All requested risk_type_codes are outside risk universe; falling back to defaults")
    return list(ACHIEVEMENT_RISK_TYPE_CODES_DEFAULT), True


def split_business_vs_opportunity_risk_codes(risk_type_codes: List[str]) -> Tuple[List[str], List[str]]:
    """Split risk codes into business-level vs opportunity/customer-level groups."""
    business_codes = [c for c in risk_type_codes if c in BUSINESS_RISK_TYPE_CODES]
    opportunity_codes = [c for c in risk_type_codes if c not in BUSINESS_RISK_TYPE_CODES]
    return business_codes, opportunity_codes


def resolve_business_relation_type_names(risk_type_codes: List[str]) -> List[str]:
    """Map business risk type codes to relation-table type_name filters (deduplicated)."""
    names: List[str] = []
    seen: set[str] = set()
    for code in risk_type_codes:
        name = BUSINESS_RISK_RELATION_TYPE_NAME_BY_CODE.get(code)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names
