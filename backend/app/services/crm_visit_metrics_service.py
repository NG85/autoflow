from __future__ import annotations

import logging
from dataclasses import dataclass
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


def _week_sun_sat_containing(d: date) -> tuple[date, date]:
    """
    周口径：周日~周六（北京时间）。
    """
    # Python: weekday(): Mon=0..Sun=6
    days_since_sunday = (d.weekday() + 1) % 7
    week_start = d - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


@dataclass
class RebuildWindows:
    entry_week_starts: list[date]
    followup_start: date
    followup_end: date


def default_rebuild_windows() -> RebuildWindows:
    today = beijing_today_date()
    this_week_start, _ = _week_sun_sat_containing(today)
    prev_week_start = this_week_start - timedelta(days=7)
    followup_days = int(getattr(settings, "CRM_VISIT_METRICS_FOLLOWUP_DAYS", 60) or 60)
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

    def rebuild_entry_week(
        self,
        session: Session,
        week_start: date,
        week_end: Optional[date] = None,
    ) -> dict[str, int]:
        """
        重算一个 entry-week 的结果（写入 EAV 事实表 crm_visit_metrics_facts）：
        - anchor=entry, grain=week, subject=sales：周×人×部门（total_visits/call_high_visits）
        - anchor=entry, grain=day, subject=department：日×部门（带 weekday_iso，用于“录入星期分布”）
        - anchor=entry, grain=week, subject=company：公司级人数类指标（sales_with_visits / sales_headcount）
        同时对该周范围内的拜访记录做部门快照回填（若为空）。
        """
        if week_end is None:
            _, week_end = _week_sun_sat_containing(week_start)
        utc_start, utc_end = self._utc_range_for_beijing_date_span(week_start, week_end)
        backfilled = 0

        # 1) 周×人×部门
        weekly_rows = session.exec(
            text(
                """
                SELECT
                  recorder_id AS recorder_id,
                  COALESCE(recorder_department_id, '') AS department_id,
                  COALESCE(recorder_department_name, '') AS department_name,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE last_modified_time >= :utc_start
                  AND last_modified_time <= :utc_end
                GROUP BY recorder_id, department_id, department_name
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).all()

        upsert_fact = text(
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

        weekly_written = 0  # upsert 行数（EAV 后= 2 * group rows）
        for recorder_id, department_id, department_name, total_visits, call_high_visits in weekly_rows:
            if not recorder_id:
                continue
            dept_id = str(department_id or "")
            base = {
                "anchor": "entry",
                "grain": "week",
                "period_start": week_start,
                "period_end": week_end,
                "subject_type": "sales",
                "subject_id": str(recorder_id),
                "department_id": dept_id,
                "department_name": str(department_name or ""),
                "weekday_iso": 0,
            }
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base,
                    "metric": "total_visits",
                    "value_int": int(total_visits or 0),
                },
            )
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base,
                    "metric": "call_high_visits",
                    "value_int": int(call_high_visits or 0),
                },
            )
            weekly_written += 2

        # 2) 日×部门（entry_date 为北京时间日期）
        daily_rows = session.exec(
            text(
                """
                SELECT
                  DATE(DATE_ADD(last_modified_time, INTERVAL 8 HOUR)) AS entry_date,
                  COALESCE(recorder_department_id, '') AS department_id,
                  COALESCE(recorder_department_name, '') AS department_name,
                  (WEEKDAY(DATE(DATE_ADD(last_modified_time, INTERVAL 8 HOUR))) + 1) AS weekday_iso,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE last_modified_time >= :utc_start
                  AND last_modified_time <= :utc_end
                GROUP BY entry_date, department_id, department_name, weekday_iso
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).all()

        daily_written = 0  # upsert 行数（EAV 后= 2 * group rows）
        for entry_date, department_id, department_name, weekday_iso, total_visits, call_high_visits in daily_rows:
            if not entry_date or department_id is None:
                continue
            base = {
                "anchor": "entry",
                "grain": "day",
                "period_start": entry_date,
                "period_end": entry_date,
                "subject_type": "department",
                "subject_id": str(department_id or ""),
                "department_id": str(department_id or ""),
                "department_name": str(department_name or ""),
                "weekday_iso": int(weekday_iso or 0),
            }
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base,
                    "metric": "total_visits",
                    "value_int": int(total_visits or 0),
                },
            )
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base,
                    "metric": "call_high_visits",
                    "value_int": int(call_high_visits or 0),
                },
            )
            daily_written += 2

        # 3) 周×公司×weekday（录入时间星期分布，7桶）
        company_weekday_rows = session.exec(
            text(
                """
                SELECT
                  (WEEKDAY(DATE(DATE_ADD(last_modified_time, INTERVAL 8 HOUR))) + 1) AS weekday_iso,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE last_modified_time >= :utc_start
                  AND last_modified_time <= :utc_end
                GROUP BY weekday_iso
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).all()

        weekday_map: dict[int, tuple[int, int]] = {}
        for weekday_iso, total_visits, call_high_visits in company_weekday_rows:
            try:
                w = int(weekday_iso or 0)
            except Exception:
                continue
            if w < 1 or w > 7:
                continue
            weekday_map[w] = (int(total_visits or 0), int(call_high_visits or 0))

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
            total_v, call_high_v = weekday_map.get(w, (0, 0))
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base_company,
                    "metric": "total_visits",
                    "weekday_iso": w,
                    "value_int": int(total_v),
                },
            )
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    **base_company,
                    "metric": "call_high_visits",
                    "weekday_iso": w,
                    "value_int": int(call_high_v),
                },
            )
            company_written += 2

        # 4) 公司级人数类指标（便于下游直接计算两类人均）
        # - sales_with_visits：当周有拜访记录的销售人数（去重 recorder_id）
        # - sales_headcount：全公司销售总人数（按 user_department_relation 表，去重 crm_user_id）
        def _row_to_int(v: object) -> int:
            # SQLModel/SQLAlchemy 可能返回 Row/tuple/scalar，统一转为 int
            if v is None:
                return 0
            if isinstance(v, (int, float)):
                return int(v)
            try:
                # RowMapping: row.cnt
                cnt = getattr(v, "cnt", None)
                if cnt is not None:
                    return int(cnt)
            except Exception:
                pass
            try:
                # tuple-like Row: row[0]
                return int(v[0])  # type: ignore[index]
            except Exception:
                return int(0)

        sales_with_visits_row = session.exec(
            text(
                """
                SELECT COUNT(DISTINCT recorder_id) AS cnt
                FROM crm_sales_visit_records
                WHERE last_modified_time >= :utc_start
                  AND last_modified_time <= :utc_end
                  AND recorder_id IS NOT NULL
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).first()
        sales_with_visits = _row_to_int(sales_with_visits_row)

        sales_headcount_row = session.exec(
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
        sales_headcount = _row_to_int(sales_headcount_row)

        base_company_people = {
            "anchor": "entry",
            "grain": "week",
            "period_start": week_start,
            "period_end": week_end,
            "subject_type": "company",
            "subject_id": _SUBJECT_ID_ALL,
            "department_id": _DEPARTMENT_ID_ALL,
            "department_name": "",
            "weekday_iso": 0,
        }
        # sales_with_visits
        session.exec(
            upsert_fact,
            params={
                "id": uuid7().hex.replace("-", ""),
                **base_company_people,
                "metric": "sales_with_visits",
                "value_int": int(sales_with_visits),
            },
        )
        # sales_headcount
        session.exec(
            upsert_fact,
            params={
                "id": uuid7().hex.replace("-", ""),
                **base_company_people,
                "metric": "sales_headcount",
                "value_int": int(sales_headcount),
            },
        )

        return {
            "backfilled_snapshot_recorders": int(backfilled),
            "weekly_rows": int(weekly_written),
            "daily_rows": int(daily_written),
            "company_weekday_rows": int(company_written),
            "company_people_rows": 2,
        }

    def rebuild_followup_daily_department_metrics(
        self,
        session: Session,
        start_date: date,
        end_date: date,
    ) -> int:
        """
        重算 followup-date 维度的 日×部门 指标（写入 EAV 事实表，用于 weekday 分布）。
        """
        rows = session.exec(
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

        upsert_fact = text(
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

        written = 0  # upsert 行数（EAV 后= 2 * group rows）
        for followup_date, department_id, department_name, weekday_iso, total_visits, call_high_visits in rows:
            if not followup_date or department_id is None:
                continue
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    "anchor": "followup",
                    "grain": "day",
                    "period_start": followup_date,
                    "period_end": followup_date,
                    "subject_type": "department",
                    "subject_id": str(department_id or ""),
                    "department_id": str(department_id or ""),
                    "department_name": str(department_name or ""),
                    "metric": "total_visits",
                    "weekday_iso": int(weekday_iso or 0),
                    "value_int": int(total_visits or 0),
                },
            )
            session.exec(
                upsert_fact,
                params={
                    "id": uuid7().hex.replace("-", ""),
                    "anchor": "followup",
                    "grain": "day",
                    "period_start": followup_date,
                    "period_end": followup_date,
                    "subject_type": "department",
                    "subject_id": str(department_id or ""),
                    "department_id": str(department_id or ""),
                    "department_name": str(department_name or ""),
                    "metric": "call_high_visits",
                    "weekday_iso": int(weekday_iso or 0),
                    "value_int": int(call_high_visits or 0),
                },
            )
            written += 2

        # 同时固化：周×公司×weekday（实际跟进日期星期分布，7桶）
        # 将 [start_date, end_date] 覆盖的所有周都补齐 7 桶；缺失桶写 0，便于下游直接画图
        first_week_start, _ = _week_sun_sat_containing(start_date)
        last_week_start, _ = _week_sun_sat_containing(end_date)
        week_starts: list[date] = []
        cur = first_week_start
        while cur <= last_week_start:
            week_starts.append(cur)
            cur = cur + timedelta(days=7)

        company_weekday_agg = session.exec(
            text(
                """
                SELECT
                  DATE_SUB(visit_communication_date, INTERVAL (DAYOFWEEK(visit_communication_date) - 1) DAY) AS week_start,
                  (WEEKDAY(visit_communication_date) + 1) AS weekday_iso,
                  COUNT(1) AS total_visits,
                  SUM(CASE WHEN is_call_high = 1 THEN 1 ELSE 0 END) AS call_high_visits
                FROM crm_sales_visit_records
                WHERE visit_communication_date IS NOT NULL
                  AND visit_communication_date >= :start_date
                  AND visit_communication_date <= :end_date
                GROUP BY week_start, weekday_iso
                """
            ),
            params={"start_date": start_date, "end_date": end_date},
        ).all()

        # week_start(date) -> weekday(1..7) -> (total, call_high)
        wk_map: dict[date, dict[int, tuple[int, int]]] = {}
        for ws, weekday_iso, total_visits, call_high_visits in company_weekday_agg:
            try:
                w = int(weekday_iso or 0)
            except Exception:
                continue
            if w < 1 or w > 7 or not ws:
                continue
            wk_map.setdefault(ws, {})[w] = (int(total_visits or 0), int(call_high_visits or 0))

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
                session.exec(
                    upsert_fact,
                    params={
                        "id": uuid7().hex.replace("-", ""),
                        **base_company,
                        "metric": "total_visits",
                        "weekday_iso": w,
                        "value_int": int(total_v),
                    },
                )
                session.exec(
                    upsert_fact,
                    params={
                        "id": uuid7().hex.replace("-", ""),
                        **base_company,
                        "metric": "call_high_visits",
                        "weekday_iso": w,
                        "value_int": int(call_high_v),
                    },
                )
                company_written += 2

        return int(written + company_written)


crm_visit_metrics_service = CRMVisitMetricsService()

