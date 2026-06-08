from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings
from app.utils.date_utils import beijing_today_date, convert_beijing_date_to_utc_range
from app.utils.uuid6 import uuid7

logger = logging.getLogger(__name__)

_SUBJECT_ID_ALL = "__ALL__"
_DEPARTMENT_ID_ALL = "__ALL__"
_BATCH_UPSERT_SIZE = 500

_FACT_UPSERT_SQL = text(
    """
    INSERT INTO crm_visit_metrics_facts
      (id, anchor, grain, period_start, period_end, subject_type, subject_id, department_id, department_name, metric, weekday_iso, value_int)
    VALUES
      (:id, :anchor, :grain, :period_start, :period_end, :subject_type, :subject_id, :department_id, :department_name, :metric, :weekday_iso, :value_int)
    ON DUPLICATE KEY UPDATE
      department_name = VALUES(department_name),
      value_int = VALUES(value_int),
      updated_at = CURRENT_TIMESTAMP
    """
)


def _week_sun_sat_containing(d: date) -> tuple[date, date]:
    """
    周口径：周日~周六（北京时间）。
    """
    days_since_sunday = (d.weekday() + 1) % 7
    week_start = d - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _row_to_int(v: object) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        cnt = getattr(v, "cnt", None)
        if cnt is not None:
            return int(cnt)
    except Exception:
        pass
    try:
        return int(v[0])  # type: ignore[index]
    except Exception:
        return 0


def _field_from_row(r: object, name: str, index: int, default: object = "") -> object:
    value = getattr(r, name, None)
    if value is None:
        try:
            value = r[index]  # type: ignore[index]
        except Exception:
            value = default
    return value if value is not None else default


@dataclass
class RebuildWindows:
    entry_week_starts: list[date]
    followup_start: date
    followup_end: date


@dataclass
class VisitMetricsContext:
    """任务级缓存：与周无关的指标只查一次。"""

    sales_headcount: int | None = field(default=None)

    def get_sales_headcount(self, session: Session) -> int:
        if self.sales_headcount is not None:
            return self.sales_headcount
        row = session.exec(
            text(
                """
                SELECT COUNT(DISTINCT crm_user_id) AS cnt
                FROM user_department_relation
                WHERE is_active = 1
                  AND crm_user_id IS NOT NULL AND crm_user_id <> ''
                  AND department_id IS NOT NULL AND department_id <> ''
                """
            )
        ).first()
        self.sales_headcount = _row_to_int(row)
        return self.sales_headcount


def default_rebuild_windows() -> RebuildWindows:
    today = beijing_today_date()
    this_week_start, _ = _week_sun_sat_containing(today)
    prev_week_start = this_week_start - timedelta(days=7)
    followup_days = int(settings.CRM_VISIT_METRICS_FOLLOWUP_DAYS or 7)
    followup_end = today
    followup_start = today - timedelta(days=max(followup_days - 1, 0))
    return RebuildWindows(
        entry_week_starts=[prev_week_start, this_week_start],
        followup_start=followup_start,
        followup_end=followup_end,
    )


class CRMVisitMetricsService:
    """
    CRM 拜访指标固化服务：
    - entry-week：基于 last_modified_time（UTC）折算为北京时间日期后归周（周日~周六）
    - followup：基于 visit_communication_date（date）按日期固化（用于 weekday 分布）
    """

    def _utc_range_for_beijing_date_span(self, start: date, end: date) -> tuple[datetime, datetime]:
        utc_start = convert_beijing_date_to_utc_range(start.isoformat(), is_start=True)
        utc_end = convert_beijing_date_to_utc_range(end.isoformat(), is_start=False)
        if utc_start is None or utc_end is None:
            raise ValueError(f"Invalid date span: {start}~{end}")
        return utc_start, utc_end

    @staticmethod
    def _fact_row(
        *,
        anchor: str,
        grain: str,
        period_start: date,
        period_end: date,
        subject_type: str,
        subject_id: str,
        department_id: str,
        department_name: str,
        metric: str,
        weekday_iso: int,
        value_int: int,
    ) -> dict:
        return {
            "id": uuid7().hex.replace("-", ""),
            "anchor": anchor,
            "grain": grain,
            "period_start": period_start,
            "period_end": period_end,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "department_id": department_id,
            "department_name": department_name,
            "metric": metric,
            "weekday_iso": int(weekday_iso or 0),
            "value_int": int(value_int or 0),
        }

    @staticmethod
    def _append_metric_pair(
        rows: list[dict],
        base: dict,
        total_visits: int,
        call_high_visits: int,
    ) -> None:
        rows.append(CRMVisitMetricsService._fact_row(**base, metric="total_visits", value_int=int(total_visits or 0)))
        rows.append(
            CRMVisitMetricsService._fact_row(**base, metric="call_high_visits", value_int=int(call_high_visits or 0))
        )

    def _batch_upsert_fact_metrics(self, session: Session, rows: list[dict]) -> int:
        if not rows:
            return 0
        conn = session.connection()
        for i in range(0, len(rows), _BATCH_UPSERT_SIZE):
            conn.execute(_FACT_UPSERT_SQL, rows[i : i + _BATCH_UPSERT_SIZE])
        return len(rows)

    def _load_entry_week_detail_rows(self, session: Session, utc_start: datetime, utc_end: datetime) -> list:
        return session.exec(
            text(
                """
                SELECT
                  recorder_id,
                  COALESCE(recorder_department_id, '') AS department_id,
                  COALESCE(recorder_department_name, '') AS department_name,
                  DATE(DATE_ADD(last_modified_time, INTERVAL 8 HOUR)) AS entry_date,
                  (WEEKDAY(DATE(DATE_ADD(last_modified_time, INTERVAL 8 HOUR))) + 1) AS weekday_iso,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE last_modified_time >= :utc_start
                  AND last_modified_time <= :utc_end
                GROUP BY recorder_id, department_id, department_name, entry_date, weekday_iso
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).all()

    def _build_entry_week_fact_rows(
        self,
        detail_rows: list,
        week_start: date,
        week_end: date,
        sales_headcount: int,
    ) -> tuple[list[dict], dict[str, int]]:
        weekly_agg: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
        daily_agg: dict[tuple[date, str, str, int], list[int]] = defaultdict(lambda: [0, 0])
        weekday_agg: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        recorder_ids: set[str] = set()

        for r in detail_rows:
            recorder_id = str(_field_from_row(r, "recorder_id", 0, "") or "").strip()
            department_id = str(_field_from_row(r, "department_id", 1, "") or "")
            department_name = str(_field_from_row(r, "department_name", 2, "") or "")
            entry_date = _field_from_row(r, "entry_date", 3, None)
            weekday_iso = int(_field_from_row(r, "weekday_iso", 4, 0) or 0)
            total_visits = int(_field_from_row(r, "total_visits", 5, 0) or 0)
            call_high_visits = int(_field_from_row(r, "call_high_visits", 6, 0) or 0)

            if recorder_id:
                recorder_ids.add(recorder_id)
                weekly_key = (recorder_id, department_id, department_name)
                weekly_agg[weekly_key][0] += total_visits
                weekly_agg[weekly_key][1] += call_high_visits

            if entry_date and department_id is not None:
                daily_key = (entry_date, department_id, department_name, weekday_iso)
                daily_agg[daily_key][0] += total_visits
                daily_agg[daily_key][1] += call_high_visits

            if 1 <= weekday_iso <= 7:
                weekday_agg[weekday_iso][0] += total_visits
                weekday_agg[weekday_iso][1] += call_high_visits

        fact_rows: list[dict] = []
        weekly_written = 0
        for (recorder_id, department_id, department_name), (total_visits, call_high_visits) in weekly_agg.items():
            base = {
                "anchor": "entry",
                "grain": "week",
                "period_start": week_start,
                "period_end": week_end,
                "subject_type": "sales",
                "subject_id": recorder_id,
                "department_id": department_id,
                "department_name": department_name,
                "weekday_iso": 0,
            }
            self._append_metric_pair(fact_rows, base, total_visits, call_high_visits)
            weekly_written += 2

        daily_written = 0
        for (entry_date, department_id, department_name, weekday_iso), (total_visits, call_high_visits) in daily_agg.items():
            base = {
                "anchor": "entry",
                "grain": "day",
                "period_start": entry_date,
                "period_end": entry_date,
                "subject_type": "department",
                "subject_id": department_id,
                "department_id": department_id,
                "department_name": department_name,
                "weekday_iso": weekday_iso,
            }
            self._append_metric_pair(fact_rows, base, total_visits, call_high_visits)
            daily_written += 2

        company_written = 0
        base_company = {
            "anchor": "entry",
            "grain": "week",
            "period_start": week_start,
            "period_end": week_end,
            "subject_type": "company",
            "subject_id": _SUBJECT_ID_ALL,
            "department_id": _DEPARTMENT_ID_ALL,
            "department_name": "",
        }
        for w in range(1, 8):
            total_visits, call_high_visits = weekday_agg.get(w, [0, 0])
            self._append_metric_pair(
                fact_rows,
                {**base_company, "weekday_iso": w},
                total_visits,
                call_high_visits,
            )
            company_written += 2

        company_total_visits = sum(weekday_agg.get(w, [0, 0])[0] for w in range(1, 8))
        company_total_call_high = sum(weekday_agg.get(w, [0, 0])[1] for w in range(1, 8))
        self._append_metric_pair(
            fact_rows,
            {**base_company, "weekday_iso": 0},
            company_total_visits,
            company_total_call_high,
        )
        company_written += 2

        base_company_people = {**base_company, "weekday_iso": 0}
        fact_rows.append(
            self._fact_row(
                **base_company_people,
                metric="sales_with_visits",
                value_int=len(recorder_ids),
            )
        )
        fact_rows.append(
            self._fact_row(
                **base_company_people,
                metric="sales_headcount",
                value_int=int(sales_headcount),
            )
        )

        counts = {
            "weekly_rows": weekly_written,
            "daily_rows": daily_written,
            "company_weekday_rows": company_written,
            "company_people_rows": 2,
        }
        return fact_rows, counts

    def rebuild_entry_week(
        self,
        session: Session,
        week_start: date,
        week_end: Optional[date] = None,
        *,
        metrics_context: VisitMetricsContext | None = None,
    ) -> dict[str, int]:
        """
        重算一个 entry-week 的结果（写入 EAV 事实表 crm_visit_metrics_facts）：
        - anchor=entry, grain=week, subject=sales：周×人×部门（total_visits/call_high_visits）
        - anchor=entry, grain=day, subject=department：日×部门（带 weekday_iso，用于“录入星期分布”）
        - anchor=entry, grain=week, subject=company：公司级人数类指标（sales_with_visits / sales_headcount）
        """
        if week_end is None:
            _, week_end = _week_sun_sat_containing(week_start)
        utc_start, utc_end = self._utc_range_for_beijing_date_span(week_start, week_end)

        ctx = metrics_context or VisitMetricsContext()
        sales_headcount = ctx.get_sales_headcount(session)
        detail_rows = self._load_entry_week_detail_rows(session, utc_start, utc_end)
        fact_rows, counts = self._build_entry_week_fact_rows(
            detail_rows,
            week_start=week_start,
            week_end=week_end,
            sales_headcount=sales_headcount,
        )
        self._batch_upsert_fact_metrics(session, fact_rows)

        return {
            "backfilled_snapshot_recorders": 0,
            **counts,
        }

    def _load_followup_detail_rows(self, session: Session, start_date: date, end_date: date) -> list:
        return session.exec(
            text(
                """
                SELECT
                  visit_communication_date AS followup_date,
                  COALESCE(recorder_department_id, '') AS department_id,
                  COALESCE(recorder_department_name, '') AS department_name,
                  (WEEKDAY(visit_communication_date) + 1) AS weekday_iso,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE visit_communication_date IS NOT NULL
                  AND visit_communication_date >= :start_date
                  AND visit_communication_date <= :end_date
                GROUP BY followup_date, department_id, department_name, weekday_iso
                """
            ),
            params={"start_date": start_date, "end_date": end_date},
        ).all()

    def rebuild_followup_daily_department_metrics(
        self,
        session: Session,
        start_date: date,
        end_date: date,
    ) -> int:
        """
        重算 followup-date 维度的 日×部门 指标（写入 EAV 事实表，用于 weekday 分布）。
        """
        rows = self._load_followup_detail_rows(session, start_date, end_date)

        fact_rows: list[dict] = []
        written = 0
        wk_map: dict[date, dict[int, tuple[int, int]]] = defaultdict(dict)

        for r in rows:
            followup_date = _field_from_row(r, "followup_date", 0, None)
            department_id = _field_from_row(r, "department_id", 1, "")
            department_name = _field_from_row(r, "department_name", 2, "")
            weekday_iso = int(_field_from_row(r, "weekday_iso", 3, 0) or 0)
            total_visits = int(_field_from_row(r, "total_visits", 4, 0) or 0)
            call_high_visits = int(_field_from_row(r, "call_high_visits", 5, 0) or 0)

            if not followup_date or department_id is None:
                continue

            dept_id = str(department_id or "")
            base = {
                "anchor": "followup",
                "grain": "day",
                "period_start": followup_date,
                "period_end": followup_date,
                "subject_type": "department",
                "subject_id": dept_id,
                "department_id": dept_id,
                "department_name": str(department_name or ""),
                "weekday_iso": weekday_iso,
            }
            self._append_metric_pair(fact_rows, base, total_visits, call_high_visits)
            written += 2

            if 1 <= weekday_iso <= 7:
                week_start, _ = _week_sun_sat_containing(followup_date)
                prev_total, prev_call_high = wk_map[week_start].get(weekday_iso, (0, 0))
                wk_map[week_start][weekday_iso] = (
                    prev_total + total_visits,
                    prev_call_high + call_high_visits,
                )

        first_week_start, _ = _week_sun_sat_containing(start_date)
        last_week_start, _ = _week_sun_sat_containing(end_date)
        week_starts: list[date] = []
        cur = first_week_start
        while cur <= last_week_start:
            week_starts.append(cur)
            cur = cur + timedelta(days=7)

        company_written = 0
        for ws in week_starts:
            we = ws + timedelta(days=6)
            base_company = {
                "anchor": "followup",
                "grain": "week",
                "period_start": ws,
                "period_end": we,
                "subject_type": "company",
                "subject_id": _SUBJECT_ID_ALL,
                "department_id": _DEPARTMENT_ID_ALL,
                "department_name": "",
            }
            weekday_vals = wk_map.get(ws, {})
            for w in range(1, 8):
                total_v, call_high_v = weekday_vals.get(w, (0, 0))
                self._append_metric_pair(
                    fact_rows,
                    {**base_company, "weekday_iso": w},
                    total_v,
                    call_high_v,
                )
                company_written += 2

        self._batch_upsert_fact_metrics(session, fact_rows)
        return int(written + company_written)


crm_visit_metrics_service = CRMVisitMetricsService()
