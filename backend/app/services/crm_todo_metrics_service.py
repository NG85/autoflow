from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from typing import Optional

from sqlalchemy import text
from sqlmodel import Session

from app.services.crm_sales_task_statistics_service import crm_sales_task_statistics_service
from app.utils.date_utils import beijing_today_date, convert_beijing_date_to_utc_range
from app.utils.uuid6 import uuid7

logger = logging.getLogger(__name__)


_ASSIGNEE_ALL = "__ALL__"
_DEPT_ALL = "__ALL__"


def _week_sun_sat_containing(d: date) -> tuple[date, date]:
    # Python weekday: Mon=0..Sun=6
    days_since_sunday = (d.weekday() + 1) % 7
    week_start = d - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _utc_range_for_beijing_date_span(start: date, end: date) -> tuple[datetime, datetime]:
    utc_start = convert_beijing_date_to_utc_range(start.isoformat(), is_start=True)
    utc_end = convert_beijing_date_to_utc_range(end.isoformat(), is_start=False)
    if utc_start is None or utc_end is None:
        raise ValueError(f"Invalid date span: {start}~{end}")
    return utc_start, utc_end


@dataclass
class TodoMetricsWindows:
    week_starts: list[date]


def default_todo_metrics_windows() -> TodoMetricsWindows:
    today = beijing_today_date()
    this_week_start, _ = _week_sun_sat_containing(today)
    prev_week_start = this_week_start - timedelta(days=7)
    return TodoMetricsWindows(week_starts=[prev_week_start, this_week_start])


class CRMTodoMetricsService:
    """
    crm_todos 指标固化：
    - 继续写入既有周指标表 crm_todos_weekly_metrics（不改表结构）
    - 并行写入 todo 域 facts 表 crm_todo_metrics_facts（EAV），用于后续从周升级到日/小时粒度

    新增指标：
    - metric=created（按创建时间落周），data_source=MANUAL（以及可选 __ALL__）
    - metric=no_due_date_stock_total（due_date 为空的存量快照，总数）
    - metric=no_due_date_stock_{status}（due_date 为空的存量快照，按 ai_status 分桶）
    - metric=status_{pending|in_progress|completed|cancelled}（状态存量快照）

    说明：
    - 任务创建时间使用 crm_todos.create_time（你已确认该列即创建时间）
    - company 级 due_date IS NULL 存量三条线（按 data_source）会同时落到 weekly 表与 facts 表
    """

    def _upsert_weekly_metric(
        self,
        session: Session,
        week_start: date,
        week_end: date,
        assignee_id: str,
        department_id: str,
        metric: str,
        data_source: str,
        value: int,
    ) -> None:
        session.exec(
            text(
                """
                INSERT INTO crm_todos_weekly_metrics
                    (id, week_start, week_end, assignee_id, department_id, metric, data_source, value)
                VALUES
                    (:id, :week_start, :week_end, :assignee_id, :department_id, :metric, :data_source, :value)
                ON DUPLICATE KEY UPDATE
                    value = VALUES(value),
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            params={
                "id": uuid7().hex.replace("-", ""),
                "week_start": week_start,
                "week_end": week_end,
                "assignee_id": assignee_id,
                "department_id": department_id,
                "metric": metric,
                "data_source": data_source,
                "value": int(value or 0),
            },
        )

    def _upsert_fact_metric(
        self,
        session: Session,
        *,
        anchor: str,
        grain: str,
        period_start: date,
        period_end: date,
        hour_of_day: int,
        subject_type: str,
        subject_id: str,
        data_source: str,
        metric: str,
        value_int: int,
    ) -> None:
        session.exec(
            text(
                """
                INSERT INTO crm_todo_metrics_facts
                    (
                        id,
                        anchor,
                        grain,
                        period_start,
                        period_end,
                        hour_of_day,
                        subject_type,
                        subject_id,
                        data_source,
                        metric,
                        value_int
                    )
                VALUES
                    (
                        :id,
                        :anchor,
                        :grain,
                        :period_start,
                        :period_end,
                        :hour_of_day,
                        :subject_type,
                        :subject_id,
                        :data_source,
                        :metric,
                        :value_int
                    )
                ON DUPLICATE KEY UPDATE
                    value_int = VALUES(value_int),
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            params={
                "id": uuid7().hex.replace("-", ""),
                "anchor": anchor,
                "grain": grain,
                "period_start": period_start,
                "period_end": period_end,
                "hour_of_day": int(hour_of_day or 0),
                "subject_type": subject_type,
                "subject_id": subject_id,
                "data_source": data_source,
                "metric": metric,
                "value_int": int(value_int or 0),
            },
        )

    def rebuild_weekly_manual_created(
        self,
        session: Session,
        week_start: date,
        week_end: date,
    ) -> dict[str, int]:
        """
        每周自创建任务数（按创建时间落周）。
        """
        utc_start, utc_end = _utc_range_for_beijing_date_span(week_start, week_end)

        rows = session.exec(
            text(
                """
                SELECT
                  owner_id,
                  owner_name,
                  COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source = 'MANUAL'
                  AND create_time IS NOT NULL
                  AND create_time >= :utc_start
                  AND create_time <= :utc_end
                GROUP BY owner_id, owner_name
                """
            ),
            params={"utc_start": utc_start, "utc_end": utc_end},
        ).fetchall()

        # assignee_id 选择优先 owner_id，其次 owner_name
        assignee_ids = []
        key_rows: list[tuple[str, int]] = []
        for r in rows:
            owner_id = str(getattr(r, "owner_id", None) or (r[0] if len(r) > 0 else "") or "").strip()
            owner_name = str(getattr(r, "owner_name", None) or (r[1] if len(r) > 1 else "") or "").strip()
            cnt = int(getattr(r, "cnt", None) or (r[2] if len(r) > 2 else 0) or 0)
            assignee_id = owner_id or owner_name
            if not assignee_id:
                continue
            assignee_ids.append(assignee_id)
            key_rows.append((assignee_id, cnt))

        raw_to_resolved, raw_to_dept = crm_sales_task_statistics_service._map_assignee_to_department_id(session, assignee_ids)  # noqa: SLF001

        total_company = 0
        written = 0
        for raw_assignee_id, cnt in key_rows:
            resolved = raw_to_resolved.get(raw_assignee_id, raw_assignee_id)
            dept = raw_to_dept.get(raw_assignee_id, "UNKNOWN")
            self._upsert_weekly_metric(
                session,
                week_start,
                week_end,
                assignee_id=resolved,
                department_id=dept,
                metric="tasks_created_manual",
                data_source="MANUAL",
                value=cnt,
            )
            # facts：周粒度 created（manual）
            self._upsert_fact_metric(
                session,
                anchor="created",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="assignee",
                subject_id=resolved,
                data_source="MANUAL",
                metric="tasks_created_manual",
                value_int=int(cnt or 0),
            )
            total_company += int(cnt)
            written += 1

        # 公司汇总
        self._upsert_weekly_metric(
            session,
            week_start,
            week_end,
            assignee_id=_ASSIGNEE_ALL,
            department_id=_DEPT_ALL,
            metric="tasks_created_manual",
            data_source="MANUAL",
            value=total_company,
        )
        self._upsert_fact_metric(
            session,
            anchor="created",
            grain="week",
            period_start=week_start,
            period_end=week_end,
            hour_of_day=0,
            subject_type="company",
            subject_id=_ASSIGNEE_ALL,
            data_source="MANUAL",
            metric="tasks_created_manual",
            value_int=int(total_company or 0),
        )
        written += 1

        return {"rows": written}

    def rebuild_weekly_completed_by_due_date(
        self,
        session: Session,
        week_start: date,
        week_end: date,
        *,
        include_sources: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """
        每周任务完成数（你定义的口径）：
        - due_date 落在周期内
        - ai_status = COMPLETED

        落库（facts）：
        - anchor=due_week
        - grain=week
        - subject_type=assignee/company
        - data_source 按来源分桶，同时写 __ALL__ 总计
        - metric=tasks_completed_by_due_date
        """
        sources = include_sources or ["MANUAL", "AI_EXTRACTION", "PIPELINE_PLAYBOOK"]

        rows = session.exec(
            text(
                """
                SELECT owner_id, owner_name, data_source, COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source IS NOT NULL
                  AND data_source IN :sources
                  AND ai_status = 'COMPLETED'
                  AND due_date IS NOT NULL
                  AND due_date >= :week_start
                  AND due_date <= :week_end
                  AND (owner_id IS NOT NULL OR owner_name IS NOT NULL)
                GROUP BY owner_id, owner_name, data_source
                """
            ),
            params={"sources": tuple(sources), "week_start": week_start, "week_end": week_end},
        ).fetchall()

        # owner_key -> data_source -> cnt
        per_owner: dict[str, dict[str, int]] = {}
        owner_keys: list[str] = []
        for r in rows:
            owner_id = str(getattr(r, "owner_id", None) or (r[0] if len(r) > 0 else "") or "").strip()
            owner_name = str(getattr(r, "owner_name", None) or (r[1] if len(r) > 1 else "") or "").strip()
            ds = str(getattr(r, "data_source", None) or (r[2] if len(r) > 2 else "") or "").strip()
            cnt = int(getattr(r, "cnt", None) or (r[3] if len(r) > 3 else 0) or 0)
            if ds not in sources:
                continue
            key = owner_id or owner_name
            if not key:
                continue
            if key not in per_owner:
                per_owner[key] = {}
                owner_keys.append(key)
            per_owner[key][ds] = per_owner[key].get(ds, 0) + cnt

        raw_to_resolved, _raw_to_dept = crm_sales_task_statistics_service._map_assignee_to_department_id(session, owner_keys)  # noqa: SLF001

        written = 0
        company_by_source = {ds: 0 for ds in sources}

        for raw_owner, ds_map in per_owner.items():
            resolved = raw_to_resolved.get(raw_owner, raw_owner)
            total_owner = 0
            for ds in sources:
                v = int(ds_map.get(ds, 0))
                total_owner += v
                company_by_source[ds] += v
                self._upsert_fact_metric(
                    session,
                    anchor="due_week",
                    grain="week",
                    period_start=week_start,
                    period_end=week_end,
                    hour_of_day=0,
                    subject_type="assignee",
                    subject_id=resolved,
                    data_source=ds,
                    metric="tasks_completed_by_due_date",
                    value_int=v,
                )
                written += 1

            # per-owner total
            self._upsert_fact_metric(
                session,
                anchor="due_week",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="assignee",
                subject_id=resolved,
                data_source="__ALL__",
                metric="tasks_completed_by_due_date",
                value_int=int(total_owner),
            )
            written += 1

        # company: by source + total
        company_total = 0
        for ds in sources:
            v = int(company_by_source.get(ds, 0))
            company_total += v
            self._upsert_fact_metric(
                session,
                anchor="due_week",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="company",
                subject_id=_ASSIGNEE_ALL,
                data_source=ds,
                metric="tasks_completed_by_due_date",
                value_int=v,
            )
            written += 1

        self._upsert_fact_metric(
            session,
            anchor="due_week",
            grain="week",
            period_start=week_start,
            period_end=week_end,
            hour_of_day=0,
            subject_type="company",
            subject_id=_ASSIGNEE_ALL,
            data_source="__ALL__",
            metric="tasks_completed_by_due_date",
            value_int=int(company_total),
        )
        written += 1

        return {"rows": written}

    def rebuild_stock_metrics_for_week(
        self,
        session: Session,
        week_start: date,
        week_end: date,
    ) -> dict[str, int]:
        """
        存量类指标：写到当前周的 week_start/week_end 下（每次重算覆盖）。
        - no_due_date_stock
        - status distribution
        """
        statuses = ["PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"]

        # 1) due_date IS NULL 的存量（按负责人 + 状态分桶）
        rows = session.exec(
            text(
                """
                SELECT owner_id, owner_name, ai_status, COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source IS NOT NULL
                  AND due_date IS NULL
                  AND ai_status IS NOT NULL
                  AND (owner_id IS NOT NULL OR owner_name IS NOT NULL)
                GROUP BY owner_id, owner_name, ai_status
                """
            )
        ).fetchall()

        # owner -> status -> cnt
        per_owner_due_null: dict[str, dict[str, int]] = {}
        owner_keys: list[str] = []
        for r in rows:
            owner_id = str(getattr(r, "owner_id", None) or (r[0] if len(r) > 0 else "") or "").strip()
            owner_name = str(getattr(r, "owner_name", None) or (r[1] if len(r) > 1 else "") or "").strip()
            st = str(getattr(r, "ai_status", None) or (r[2] if len(r) > 2 else "") or "").strip().upper()
            cnt = int(getattr(r, "cnt", None) or (r[3] if len(r) > 3 else 0) or 0)
            if st not in statuses:
                continue
            key = owner_id or owner_name
            if not key:
                continue
            if key not in per_owner_due_null:
                per_owner_due_null[key] = {}
                owner_keys.append(key)
            per_owner_due_null[key][st] = per_owner_due_null[key].get(st, 0) + cnt

        raw_to_resolved, raw_to_dept = crm_sales_task_statistics_service._map_assignee_to_department_id(session, owner_keys)  # noqa: SLF001

        written = 0
        company_totals_by_status = {st: 0 for st in statuses}
        company_total = 0

        for raw_owner, st_map in per_owner_due_null.items():
            resolved = raw_to_resolved.get(raw_owner, raw_owner)
            dept = raw_to_dept.get(raw_owner, "UNKNOWN")

            total_for_owner = 0
            for st in statuses:
                cnt = int(st_map.get(st, 0))
                total_for_owner += cnt
                company_totals_by_status[st] += cnt
                self._upsert_weekly_metric(
                    session,
                    week_start,
                    week_end,
                    assignee_id=resolved,
                    department_id=dept,
                    metric=f"no_due_date_stock_{st.lower()}",
                    data_source="",
                    value=cnt,
                )
                self._upsert_fact_metric(
                    session,
                    anchor="stock",
                    grain="week",
                    period_start=week_start,
                    period_end=week_end,
                    hour_of_day=0,
                    subject_type="assignee",
                    subject_id=resolved,
                    data_source="",
                    metric=f"no_due_date_stock_{st.lower()}",
                    value_int=cnt,
                )
                written += 1

            company_total += total_for_owner
            # 同时写一条 total，方便下游直接取总量
            self._upsert_weekly_metric(
                session,
                week_start,
                week_end,
                assignee_id=resolved,
                department_id=dept,
                metric="no_due_date_stock_total",
                data_source="",
                value=total_for_owner,
            )
            self._upsert_fact_metric(
                session,
                anchor="stock",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="assignee",
                subject_id=resolved,
                data_source="",
                metric="no_due_date_stock_total",
                value_int=int(total_for_owner),
            )
            written += 1

        # 公司汇总：按状态 + total
        for st in statuses:
            self._upsert_weekly_metric(
                session,
                week_start,
                week_end,
                assignee_id=_ASSIGNEE_ALL,
                department_id=_DEPT_ALL,
                metric=f"no_due_date_stock_{st.lower()}",
                data_source="",
                value=int(company_totals_by_status[st]),
            )
            self._upsert_fact_metric(
                session,
                anchor="stock",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="company",
                subject_id=_ASSIGNEE_ALL,
                data_source="",
                metric=f"no_due_date_stock_{st.lower()}",
                value_int=int(company_totals_by_status[st]),
            )
            written += 1

        self._upsert_weekly_metric(
            session,
            week_start,
            week_end,
            assignee_id=_ASSIGNEE_ALL,
            department_id=_DEPT_ALL,
            metric="no_due_date_stock_total",
            data_source="",
            value=int(company_total),
        )
        self._upsert_fact_metric(
            session,
            anchor="stock",
            grain="week",
            period_start=week_start,
            period_end=week_end,
            hour_of_day=0,
            subject_type="company",
            subject_id=_ASSIGNEE_ALL,
            data_source="",
            metric="no_due_date_stock_total",
            value_int=int(company_total),
        )
        written += 1

        # 2) status distribution（按负责人分组）
        status_rows = session.exec(
            text(
                """
                SELECT owner_id, owner_name, ai_status, COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source IS NOT NULL
                  AND ai_status IS NOT NULL
                  AND (owner_id IS NOT NULL OR owner_name IS NOT NULL)
                GROUP BY owner_id, owner_name, ai_status
                """
            )
        ).fetchall()

        # owner -> status -> cnt
        per_owner: dict[str, dict[str, int]] = {}
        owner_keys: list[str] = []
        for r in status_rows:
            owner_id = str(getattr(r, "owner_id", None) or (r[0] if len(r) > 0 else "") or "").strip()
            owner_name = str(getattr(r, "owner_name", None) or (r[1] if len(r) > 1 else "") or "").strip()
            st = str(getattr(r, "ai_status", None) or (r[2] if len(r) > 2 else "") or "").strip().upper()
            cnt = int(getattr(r, "cnt", None) or (r[3] if len(r) > 3 else 0) or 0)
            if st not in statuses:
                continue
            key = owner_id or owner_name
            if not key:
                continue
            if key not in per_owner:
                per_owner[key] = {}
                owner_keys.append(key)
            per_owner[key][st] = per_owner[key].get(st, 0) + cnt

        raw_to_resolved2, raw_to_dept2 = crm_sales_task_statistics_service._map_assignee_to_department_id(session, owner_keys)  # noqa: SLF001

        company_status_totals = {st: 0 for st in statuses}
        for raw_owner, st_map in per_owner.items():
            resolved = raw_to_resolved2.get(raw_owner, raw_owner)
            dept = raw_to_dept2.get(raw_owner, "UNKNOWN")
            for st in statuses:
                cnt = int(st_map.get(st, 0))
                self._upsert_weekly_metric(
                    session,
                    week_start,
                    week_end,
                    assignee_id=resolved,
                    department_id=dept,
                    metric=f"status_{st.lower()}",
                    data_source="",
                    value=cnt,
                )
                self._upsert_fact_metric(
                    session,
                    anchor="stock",
                    grain="week",
                    period_start=week_start,
                    period_end=week_end,
                    hour_of_day=0,
                    subject_type="assignee",
                    subject_id=resolved,
                    data_source="",
                    metric=f"status_{st.lower()}",
                    value_int=cnt,
                )
                written += 1
                company_status_totals[st] += cnt

        for st in statuses:
            self._upsert_weekly_metric(
                session,
                week_start,
                week_end,
                assignee_id=_ASSIGNEE_ALL,
                department_id=_DEPT_ALL,
                metric=f"status_{st.lower()}",
                data_source="",
                value=int(company_status_totals[st]),
            )
            self._upsert_fact_metric(
                session,
                anchor="stock",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="company",
                subject_id=_ASSIGNEE_ALL,
                data_source="",
                metric=f"status_{st.lower()}",
                value_int=int(company_status_totals[st]),
            )
            written += 1

        return {"rows": written}

    def persist_company_no_due_date_stock_by_source(
        self,
        session: Session,
        week_start: date,
        week_end: date,
        *,
        include_sources: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """
        公司级：due_date IS NULL 的存量快照（按 data_source 三条线）。

        口径：
        - 截止到统计任务执行时刻的现存量（不做 as-of 时间过滤）
        - ai_status 不过滤（PENDING/IN_PROGRESS/COMPLETED/CANCELLED 全包含）
        """
        sources = include_sources or ["MANUAL", "AI_EXTRACTION", "PIPELINE_PLAYBOOK"]

        rows = session.exec(
            text(
                """
                SELECT data_source, COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source IS NOT NULL
                  AND due_date IS NULL
                  AND data_source IN :sources
                GROUP BY data_source
                """
            ),
            params={"sources": tuple(sources)},
        ).fetchall()

        by_source: dict[str, int] = {}
        for r in rows:
            ds = str(getattr(r, "data_source", None) or (r[0] if len(r) > 0 else "") or "").strip()
            cnt = int(getattr(r, "cnt", None) or (r[1] if len(r) > 1 else 0) or 0)
            if ds:
                by_source[ds] = cnt

        written = 0
        for ds in sources:
            value = int(by_source.get(ds, 0))
            self._upsert_weekly_metric(
                session,
                week_start,
                week_end,
                assignee_id=_ASSIGNEE_ALL,
                department_id=_DEPT_ALL,
                metric="no_due_date_stock_total",
                data_source=ds,
                value=value,
            )
            # 并行写入 todo_facts（便于未来按日/小时扩展）
            self._upsert_fact_metric(
                session,
                anchor="stock",
                grain="week",
                period_start=week_start,
                period_end=week_end,
                hour_of_day=0,
                subject_type="company",
                subject_id=_ASSIGNEE_ALL,
                data_source=ds,
                metric="no_due_date_stock_total",
                value_int=value,
            )
            written += 1

        return {"rows": written}

    def persist_company_no_due_date_stock_by_source_to_facts(
        self,
        session: Session,
        *,
        grain: str,
        period_start: date,
        period_end: date,
        hour_of_day: int = 0,
        include_sources: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """
        写入 crm_todo_metrics_facts：公司级 due_date IS NULL 存量快照（按 data_source 三条线）。

        - anchor 固定为 stock
        - metric 固定为 no_due_date_stock_total
        """
        sources = include_sources or ["MANUAL", "AI_EXTRACTION", "PIPELINE_PLAYBOOK"]

        rows = session.exec(
            text(
                """
                SELECT data_source, COUNT(1) AS cnt
                FROM crm_todos
                WHERE data_source IS NOT NULL
                  AND due_date IS NULL
                  AND data_source IN :sources
                GROUP BY data_source
                """
            ),
            params={"sources": tuple(sources)},
        ).fetchall()

        by_source: dict[str, int] = {}
        for r in rows:
            ds = str(getattr(r, "data_source", None) or (r[0] if len(r) > 0 else "") or "").strip()
            cnt = int(getattr(r, "cnt", None) or (r[1] if len(r) > 1 else 0) or 0)
            if ds:
                by_source[ds] = cnt

        written = 0
        for ds in sources:
            value = int(by_source.get(ds, 0))
            self._upsert_fact_metric(
                session,
                anchor="stock",
                grain=grain,
                period_start=period_start,
                period_end=period_end,
                hour_of_day=hour_of_day,
                subject_type="company",
                subject_id=_ASSIGNEE_ALL,
                data_source=ds,
                metric="no_due_date_stock_total",
                value_int=value,
            )
            written += 1

        return {"rows": written}


crm_todo_metrics_service = CRMTodoMetricsService()

