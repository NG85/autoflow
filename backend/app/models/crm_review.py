from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Boolean,
    Numeric,
    String,
    Integer,
    JSON,
    UniqueConstraint,
)
from sqlmodel import (
    SQLModel,
    Field,
    Column,
    DateTime,
    Date,
    Text,
    Index,
    func,
)

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class CRMReviewDepartment(SQLModel, table=True):
    """List of departments that require review sessions"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_department"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique identifier (UUID)",
    )

    # Department reference
    department_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="FK → crm_department.unique_id",
    )
    department_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Department name (denormalized)",
    )
    parent_department_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Parent department ID",
    )

    # Configuration
    is_active: Optional[bool] = Field(
        default=True,
        sa_column=Column(Boolean, default=True),
        description="Whether review is enabled for this dept",
    )
    review_frequency: Optional[str] = Field(
        default="weekly",
        sa_column=Column(String(32), default="weekly"),
        description="weekly/monthly/quarterly",
    )
    include_sub_departments: Optional[bool] = Field(
        default=True,
        sa_column=Column(Boolean, default=True),
        description="Whether to include sub-depts in review",
    )

    # Metadata
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.now()
        ),
        description="创建时间",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )
    created_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="User who added this dept to review list",
    )

    __table_args__ = (
        Index("idx_review_department_department_id", "department_id"),
        Index("idx_review_department_active", "is_active"),
        Index("idx_review_department_parent_id", "parent_department_id"),
    )


class CRMReviewSession(SQLModel, table=True):
    """Review session registry with 4-phase lifecycle"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_session"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique session identifier (UUID)",
    )
    session_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Display name",
    )

    # Scope: Each session is bound to one department (with sub-depts)
    department_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="FK → crm_department.unique_id",
    )
    department_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Department name",
    )

    # Classification
    review_type: str = Field(
        default="weekly_leader",
        sa_column=Column(
            String(64),
            nullable=False,
            default="weekly_leader",
        ),
        description="Review type",
    )
    period_type: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="weekly/monthly/quarterly",
    )
    period: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="Period identifier (e.g., 2026-W10)",
    )
    period_start: date = Field(
        sa_column=Column(Date, nullable=False),
        description="Period start date",
    )
    period_end: date = Field(
        sa_column=Column(Date, nullable=False),
        description="Period end date",
    )
    fiscal_year: Optional[str] = Field(
        default=None,
        sa_column=Column(String(16)),
        description="Fiscal year",
    )

    # Lifecycle
    stage: str = Field(
        default="initial_edit",
        sa_column=Column(
            String(32),
            nullable=False,
            default="initial_edit",
        ),
        description=(
            "initial_edit/first_calculating/first_calc_ready/lead_review/second_calculating/completed"
        ),
    )
    review_phase: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
        description="not_started/edit/closed - controls if attendees can edit",
    )

    # T1-T4 configurable times
    t1_time: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="T1: Session launch",
    )
    t2_time: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="T2: First calc",
    )
    t3_time: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="T3: Open to lead",
    )
    t4_time: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="T4: Second calc",
    )

    # Phase tracking
    initial_window_open_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
        description="When initial edit window opened",
    )
    initial_window_close_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
        description="When initial edit window closed",
    )
    first_calc_start_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    first_calc_end_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    first_calc_execution_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    meeting_opened_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    meeting_opened_by_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    meeting_opened_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    meeting_closed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    meeting_open_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )
    meeting_total_duration_minutes: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )
    second_calc_start_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    second_calc_end_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    second_calc_execution_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    # Launcher
    launched_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    launched_by_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    plan_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    # Time dimensions
    report_date: date = Field(
        sa_column=Column(Date, nullable=False),
    )
    report_week_of_year: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
    )
    report_month_of_year: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
    )
    report_quarter_of_year: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
    )
    report_year: int = Field(
        sa_column=Column(Integer, nullable=False),
    )

    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.now()
        ),
        description="创建时间",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        Index("idx_review_session_unique_id", "unique_id"),
        Index("idx_review_session_stage", "stage"),
        Index(
            "idx_review_session_report_year_week",
            "report_year",
            "report_week_of_year",
        ),
        Index("idx_review_session_department_id", "department_id"),
        Index("idx_review_session_t1_time", "t1_time"),
        Index("idx_review_session_t2_time", "t2_time"),
        Index("idx_review_session_t3_time", "t3_time"),
        Index("idx_review_session_t4_time", "t4_time"),
    )


class CRMReviewAttendee(SQLModel, table=True):
    """Attendees per review session with submission tracking"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_attendee"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique identifier",
    )

    session_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="FK → crm_review_session.unique_id",
    )
    user_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="User ID",
    )
    crm_user_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="CRM user ID (matches opportunity.owner_id)",
    )

    user_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="User name",
    )
    department_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Department ID",
    )
    department_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Department name",
    )
    is_leader: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
        description="Is department lead",
    )
    is_primary_dept: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
        description="Is primary department",
    )

    # Submission tracking
    has_submitted: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
    )
    submitted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    submission_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )
    modification_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )
    last_modified_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )

    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.now()
        ),
        description="创建时间",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        Index(
            "idx_review_attendee_session_user",
            "session_id",
            "user_id",
            unique=True,
        ),
        Index("idx_review_attendee_session_id", "session_id"),
        Index("idx_review_attendee_crm_user_id", "crm_user_id"),
        Index("idx_review_attendee_has_submitted", "has_submitted"),
    )


# 批量提交允许更新的字段白名单（与 API ReviewBranchSnapshotUpdateIn 一致）
REVIEW_BRANCH_SNAPSHOT_EDITABLE_FIELDS: tuple[str, ...] = (
    "forecast_type",
    "forecast_amount",
    "opportunity_stage",
    "expected_closing_date",
)


class CRMReviewOppBranchSnapshotBasicOut(SQLModel):
    """Basic fields for review snapshot list response."""

    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique record ID",
    )
    opportunity_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    opportunity_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    # Account info
    account_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    account_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    # Owner-based identification (not session-bound)
    owner_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Owner crm_user_id",
    )
    owner_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    owner_department_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    owner_department_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    snapshot_period: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="Period (e.g., 2026-W10)",
    )

    # Editable fields
    forecast_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    forecast_amount: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
    )
    opportunity_stage: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    expected_closing_date: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    stage_stay: int = Field(
        default=0,
        sa_column=Column(Integer),
    )
    
    # AI fields
    ai_commit: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    ai_stage: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    ai_expected_closing_date: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    # Optimistic lock version
    modification_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )


class CRMReviewOppBranchSnapshot(CRMReviewOppBranchSnapshotBasicOut, table=True):
    """Owner-based branch snapshot (shared across sessions via owner_id)"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_opp_branch_snapshot"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    snapshot_date: date = Field(
        sa_column=Column(Date, nullable=False),
    )


    forecast_amount_source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64)),
    )

    # Baseline (frozen at T2)
    baseline_forecast_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    baseline_forecast_amount: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
    )
    baseline_forecast_amount_source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64)),
    )
    baseline_opportunity_stage: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    baseline_expected_closing_date: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    baseline_frozen_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )

    # CRM originals (at T1)
    crm_forecast_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    crm_forecast_amount: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
    )
    crm_forecast_amount_source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64)),
    )
    crm_opportunity_stage: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    crm_expected_closing_date: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    # AI Evaluation    
    ai_evaluated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    ai_eval_source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(10)),
    )
    ai_commit_1st: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    ai_stage_1st: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    ai_expected_closing_date_1st: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    ai_evaluated_1st_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )
    ai_commit_2nd: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    ai_stage_2nd: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    ai_expected_closing_date_2nd: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    ai_evaluated_2nd_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
    )

    # Context
    expected_closing_quarter: Optional[str] = Field(
        default=None,
        sa_column=Column(String(50)),
    )
    close_date: Optional[date] = Field(
        default=None,
        sa_column=Column(Date),
    )
    is_closed: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
    )
    customer_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    # Change tracking
    was_changed_to_commit: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
    )
    was_modified: Optional[bool] = Field(
        default=False,
        sa_column=Column(Boolean, default=False),
    )

    # Modification tracking
    last_modified_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    last_modified_by_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    initial_edit_modification_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )
    meeting_edit_modification_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
    )

    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.now()
        ),
        description="创建时间",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        Index(
            "idx_review_opp_branch_snapshot_opp_period",
            "opportunity_id",
            "snapshot_period",
            unique=True,
        ),
        Index(
            "idx_review_opp_branch_snapshot_owner_period",
            "owner_id",
            "snapshot_period",
        ),
        Index(
            "idx_review_opp_branch_snapshot_period",
            "snapshot_period",
        ),
        Index(
            "idx_review_opp_branch_snapshot_was_changed",
            "was_changed_to_commit",
        ),
    )

class CRMReviewKpiMetrics(SQLModel, table=True):
    """Structured KPI metrics per scope per review session with delta/rate"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_kpi_metrics"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    session_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )

    scope_type: str = Field(
        sa_column=Column(String(32), nullable=False),
    )
    scope_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    scope_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )
    parent_scope_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
    )

    metric_category: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    metric_name: str = Field(
        sa_column=Column(String(255), nullable=False),
    )

    # Values
    metric_value: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 4)),
        description="Current period value",
    )
    metric_value_prev: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 4)),
        description="Previous period value",
    )
    metric_delta: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 4)),
        description="Change value",
    )
    metric_rate: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(8, 4)),
        description="Rate 0-1 (e.g., 0.288 = 28.8%)",
    )
    metric_unit: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    metric_content: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
    )
    metric_content_en: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
    )

    calc_phase: Optional[str] = Field(
        default="second",
        sa_column=Column(String(32), default="second"),
    )
    period_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    period: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )
    report_date: Optional[date] = Field(
        default=None,
        sa_column=Column(Date),
    )
    report_year: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
    )
    report_week_of_year: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
    )

    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=func.now()
        ),
        description="创建时间",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        Index(
            "idx_review_kpi_session_scope_cat",
            "session_id",
            "scope_type",
            "metric_category",
        ),
        Index(
            "idx_review_kpi_session_scope_metric",
            "session_id",
            "scope_id",
            "metric_name",
        ),
        Index(
            "idx_review_kpi_period_scope_metric",
            "period",
            "scope_type",
            "metric_name",
        ),
    )


class CRMReviewOppRiskProgress(SQLModel, table=True):
    """Risk and progress tracking with scope_type pattern."""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_opp_risk_progress"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Risk/Progress record UUID",
    )
    session_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="FK to crm_review_session.unique_id",
    )
    scope_type: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="opportunity/owner/department/company",
    )
    scope_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="opp_id, owner_id, or dept_id; NULL for company scope",
    )
    department_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Department ID for direct queries without JOIN",
    )
    snapshot_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="FK to crm_review_opp_branch_snapshot (opp-level only)",
    )
    opportunity_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="CRM opportunity ID (opp-level only)",
    )
    owner_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Owner crm_user_id (opp-level only)",
    )
    record_type: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="RISK, PROGRESS, or OPP_SUMMARY",
    )
    type_code: str = Field(
        sa_column=Column(String(64), nullable=False),
        description="Risk/Progress code: STAGE_LAG, COMMIT_REDUCTION, etc.",
    )
    type_name: str = Field(
        sa_column=Column(String(128), nullable=False),
        description="Display name: 阶段滞后风险, Commit减少",
    )
    category: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64)),
        description="BRD: 分类",
    )
    level: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
        description="BRD: 层级 - opportunity/owner/department/company",
    )
    severity: Optional[str] = Field(
        default=None,
        sa_column=Column(String(16)),
        description="BRD: 等级 - High/Medium/Low (or 高/中/低)",
    )
    source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128)),
        description="BRD: 来源 - 业绩统计, 跟进记录, 任务完成情况, AI阶段评估, etc.",
    )
    metric_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64)),
        description="BRD: 指标 - AI commit, GAP, commit, upside, opportunity_stage, etc.",
    )
    ai_assessment: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="AI evaluated value",
    )
    sales_assessment: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="Sales filled value",
    )
    judgment_rule: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="BRD: 风险判断规则",
    )
    summary: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="BRD: 概述",
    )
    gap_description: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Human-readable gap summary for card display",
    )
    detail_description: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="BRD: 详情描述",
    )
    solution: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="BRD: 解决建议",
    )
    evidence: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description="Supporting evidence",
    )
    financial_impact: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
        description="Financial amount impact",
    )
    previous_value: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
        description="Previous period value for WoW comparison",
    )
    current_value: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(18, 2)),
        description="Current period value",
    )
    rate_of_change: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(8, 4)),
        description="Rate of change (-0.20 = -20%)",
    )
    status: Optional[str] = Field(
        default="ACTIVE",
        sa_column=Column(String(32), default="ACTIVE"),
        description="ACTIVE, ACKNOWLEDGED, MITIGATING, RESOLVED, CLOSED",
    )
    detected_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime),
        description="When risk was resolved",
    )
    resolved_by: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="User who resolved (if manual)",
    )
    resolution_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
        description="AUTO_RESOLVED, MANUAL_RESOLVED, EXPIRED",
    )
    resolution_note: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="Note about resolution reason",
    )
    calc_phase: str = Field(
        sa_column=Column(String(16), nullable=False),
        description="first or second calculation",
    )
    snapshot_period: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="e.g., 2026-W10",
    )
    metadata_: Optional[dict] = Field(
        default=None,
        sa_column=Column("metadata", JSON),
        description="Additional extensible attributes",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )
    created_by: Optional[str] = Field(
        default="system",
        sa_column=Column(String(255), default="system"),
    )
    updated_by: Optional[str] = Field(
        default="system",
        sa_column=Column(String(255), default="system"),
    )

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "type_code",
            "snapshot_period",
            "calc_phase",
            name="uk_scope_type_period",
        ),
        Index("idx_department", "department_id"),
        Index("idx_detected_at", "detected_at"),
        Index("idx_opportunity", "opportunity_id"),
        Index("idx_owner", "owner_id"),
        Index("idx_record_type", "record_type"),
        Index("idx_session_scope", "session_id", "scope_type", "scope_id"),
        Index("idx_snapshot_period", "snapshot_period"),
        Index("idx_status", "status"),
        Index("idx_type_code", "type_code"),
    )


class CRMReviewRiskCategory(SQLModel, table=True):
    """Risk category dictionary (code/name/group) for review risk typing."""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_risk_category"

    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: str = Field(sa_column=Column(String(255), nullable=False))
    code: str = Field(sa_column=Column(String(64), nullable=False))
    name_zh: str = Field(sa_column=Column(String(128), nullable=False))
    name_en: Optional[str] = Field(default=None, sa_column=Column(String(128)))
    category_group: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    is_active: Optional[bool] = Field(default=True, sa_column=Column(Boolean, default=True))
    sort_order: Optional[int] = Field(default=None, sa_column=Column(Integer))
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )

    __table_args__ = (
        UniqueConstraint("code", name="code"),
        Index("idx_category_group", "category_group"),
        Index("idx_is_active", "is_active"),
    )


class CRMReviewRiskOpportunityRelation(SQLModel, table=True):
    """Risk to opportunity relation for review insights."""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_risk_opportunity_relation"

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="主键ID（自增序列）",
    )
    unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="唯一ID",
    )
    risk_unique_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="关联 crm_review_opp_risk_progress.unique_id",
    )
    type_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128)),
        description="冗余的风险类型名称，用于加速查询",
    )
    session_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="复盘会话ID",
    )
    snapshot_period: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="快照周期，如 2026-W13",
    )
    calc_phase: str = Field(
        sa_column=Column(String(16), nullable=False),
        description="计算阶段 first/second",
    )
    opportunity_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="关联商机ID",
    )
    owner_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="商机负责人ID",
    )
    department_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255)),
        description="部门ID",
    )
    relation_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
        description="关联原因说明",
    )
    relation_rank: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer),
        description="排序/优先级",
    )
    relation_weight: Optional[float] = Field(
        default=None,
        sa_column=Column(Numeric(8, 4)),
        description="关联权重",
    )
    metadata_: Optional[dict] = Field(
        default=None,
        sa_column=Column("metadata", JSON),
        description="扩展信息",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
        description="创建时间",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        UniqueConstraint(
            "risk_unique_id",
            "opportunity_id",
            name="uk_risk_opp_relation",
        ),
        Index("idx_opportunity_id", "opportunity_id"),
        Index("idx_risk_unique_id", "risk_unique_id"),
        Index("idx_session_period_phase", "session_id", "snapshot_period", "calc_phase"),
    )


class CRMReviewOppAuditLog(UUIDBaseModel, UpdatableBaseModel, table=True):
    """Audit log for branch snapshot modifications.
    约定：一次“整体提交/批量提交”对应一条审计记录；old_value/new_value 以 Text 存放结构化 JSON。
    """

    model_config = {"from_attributes": True}

    __tablename__ = "crm_review_opp_audit_log"

    session_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )

    change_scope: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    
    old_value: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
    )
    
    new_value: Optional[str] = Field(
        default=None,
        sa_column=Column(Text),
    )

    change_type: str = Field(
        default="UPDATE",
        sa_column=Column(String(32), nullable=False, default="UPDATE"),
    )
    
    edit_phase: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32)),
    )

    updated_by: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    updated_by_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )

    __table_args__ = (
        Index("idx_session_id", "session_id"),
        Index("idx_updated_by_id", "updated_by_id"),
        Index("idx_updated_at", "updated_at"),
    )

