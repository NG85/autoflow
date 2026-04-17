"""Shared query_plan contract constants for review Q&A."""

ALLOWED_QUERY_ROUTES = {
    "kpi_aggregation",
    "opportunity_detail",
    "mismatch_list",
    "risk_progress",
}

ALLOWED_SCOPE_TYPES = {"company", "department", "owner", "opportunity", "customer"}

ALLOWED_TIME_MODES = {"current_only", "wow", "mom"}

