from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.user_profile import UserProfile
from app.repositories.user_department_relation import user_department_relation_repo
from app.utils.uuid6 import uuid7

logger = logging.getLogger(__name__)


class CRMSalesTaskStatisticsService:
    """
    销售任务周报统计服务

    - 写入：将个人维度的周统计结果（来自 cron_jobs._analyze_crm_todos）落到事实表
    - 读取：按 department_id / 公司总计进行聚合查询
    """

    METRIC_COMPLETED = "completed"
    METRIC_OVERDUE = "overdue"
    METRIC_NEXT_WEEK = "next_week"
    METRIC_CANCELLED = "cancelled"
    METRIC_NO_DUE_DATE = "no_due_date"

    DATA_SOURCE_ALL = "__ALL__"
    DEPARTMENT_UNKNOWN = "UNKNOWN"

    def _is_uuid_like(self, v: str) -> bool:
        # 轻量判断：36 位且包含 '-'
        return bool(v) and len(v) == 36 and "-" in v

    def _resolve_assignee_user_id(self, session: Session, assignee_ids: list[str]) -> dict[str, str]:
        """
        将输入的 assignee_id（可能是 user_id/oauth_user_id/crm_user_id）解析为 user_id(UUID 字符串)。

        返回：
            raw_assignee_id -> user_id(字符串)
        """
        ids = [x for x in (assignee_ids or []) if x]
        if not ids:
            return {}

        # 1) 如果输入本身就是 user_id（UUID字符串），直接命中
        result: dict[str, str] = {x: x for x in ids if self._is_uuid_like(x)}

        # 2) 对剩余的 id，通过 user_profiles 解析到 user_id
        remaining = [x for x in ids if x not in result]
        if not remaining:
            return result

        # 分两次查，尽量用索引：oauth_user_id / crm_user_id
        rows = session.exec(
            select(UserProfile.user_id, UserProfile.oauth_user_id, UserProfile.crm_user_id).where(
                (UserProfile.oauth_user_id.in_(remaining)) | (UserProfile.crm_user_id.in_(remaining))
            )
        ).all()

        for user_id, oauth_user_id, crm_user_id in rows:
            if not user_id:
                continue
            user_id_str = str(user_id)
            if oauth_user_id and oauth_user_id in remaining:
                result[oauth_user_id] = user_id_str
            if crm_user_id and crm_user_id in remaining:
                result[crm_user_id] = user_id_str

        return result

    def _map_assignee_to_department_id(
        self, session: Session, assignee_ids: list[str]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """
        将 assignee_id 映射为 department_id。

        优先级：
        - 优先将 assignee_id 解析为 user_id（支持：user_id/oauth_user_id/crm_user_id -> user_id）
        - 再用 user_department_relation.user_id 查主部门
        - 若解析不到 user_id，则尝试把 assignee_id 当作 crm_user_id 查主部门

        Returns:
            (raw_assignee_id -> resolved_assignee_id, raw_assignee_id -> department_id)
        """
        ids = [x for x in (assignee_ids or []) if x]
        if not ids:
            return {}, {}

        raw_to_user_id = self._resolve_assignee_user_id(session, ids)

        resolved_user_ids = sorted({v for v in raw_to_user_id.values() if v})
        by_user = user_department_relation_repo.get_primary_department_by_user_ids(session, resolved_user_ids)

        # 对于解析不到 user_id 的，兜底用 crm_user_id 映射（可能为空/不存在）
        raw_without_user = [x for x in ids if x not in raw_to_user_id]
        by_crm = user_department_relation_repo.get_primary_department_by_crm_user_ids(session, raw_without_user)

        raw_to_resolved: dict[str, str] = {}
        raw_to_dept: dict[str, str] = {}
        for raw in ids:
            resolved = raw_to_user_id.get(raw) or raw
            dept = by_user.get(resolved) or by_crm.get(raw) or self.DEPARTMENT_UNKNOWN
            raw_to_resolved[raw] = resolved
            raw_to_dept[raw] = dept

        return raw_to_resolved, raw_to_dept

    def persist_weekly_user_metrics(
        self,
        session: Session,
        analyze_results: list[dict[str, Any]],
        week_start: date,
        week_end: date,
    ) -> int:
        """
        将个人周统计结果落库（幂等 upsert）。

        Returns:
            写入/更新的指标行数（尝试写入的行数）
        """
        if not analyze_results:
            return 0

        assignee_ids = [str(x.get("assignee_id") or "") for x in analyze_results]
        raw_to_resolved, raw_to_dept = self._map_assignee_to_department_id(session, assignee_ids)

        rows: list[dict[str, Any]] = []

        def add_row(assignee_id: str, department_id: str, metric: str, data_source: str, value: int) -> None:
            rows.append(
                {
                    "id": uuid7(),
                    "week_start": week_start,
                    "week_end": week_end,
                    "assignee_id": assignee_id,
                    "department_id": department_id,
                    "metric": metric,
                    "data_source": data_source or "",
                    "value": int(value or 0),
                }
            )

        for item in analyze_results:
            raw_assignee_id = str(item.get("assignee_id") or "")
            if not raw_assignee_id:
                continue
            assignee_id = raw_to_resolved.get(raw_assignee_id, raw_assignee_id)
            department_id = raw_to_dept.get(raw_assignee_id, self.DEPARTMENT_UNKNOWN)

            completed_by_source = item.get("completed_by_source") or {}
            overdue_by_source = item.get("overdue_by_source") or {}
            next_week_by_source = item.get("next_week_by_source") or {}

            # 1) 按 data_source 拆分的三类指标
            for ds, v in completed_by_source.items():
                add_row(assignee_id, department_id, self.METRIC_COMPLETED, str(ds), int(v))
            for ds, v in overdue_by_source.items():
                add_row(assignee_id, department_id, self.METRIC_OVERDUE, str(ds), int(v))
            for ds, v in next_week_by_source.items():
                add_row(assignee_id, department_id, self.METRIC_NEXT_WEEK, str(ds), int(v))

            # 2) 三类指标的总计（data_source="__ALL__"）
            add_row(
                assignee_id,
                department_id,
                self.METRIC_COMPLETED,
                self.DATA_SOURCE_ALL,
                sum(int(v or 0) for v in completed_by_source.values()),
            )
            add_row(
                assignee_id,
                department_id,
                self.METRIC_OVERDUE,
                self.DATA_SOURCE_ALL,
                sum(int(v or 0) for v in overdue_by_source.values()),
            )
            add_row(
                assignee_id,
                department_id,
                self.METRIC_NEXT_WEEK,
                self.DATA_SOURCE_ALL,
                sum(int(v or 0) for v in next_week_by_source.values()),
            )

            # 3) 取消 / due_date 为空
            add_row(
                assignee_id,
                department_id,
                self.METRIC_CANCELLED,
                "",
                int(item.get("cancelled_count") or 0),
            )
            add_row(
                assignee_id,
                department_id,
                self.METRIC_NO_DUE_DATE,
                "",
                int(item.get("no_due_date_count") or 0),
            )

        if not rows:
            return 0

        upsert_sql = text(
            """
            INSERT INTO crm_todos_weekly_metrics
                (id, week_start, week_end, assignee_id, department_id, metric, data_source, value)
            VALUES
                (:id, :week_start, :week_end, :assignee_id, :department_id, :metric, :data_source, :value)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                updated_at = CURRENT_TIMESTAMP
            """
        )

        for r in rows:
            session.exec(upsert_sql, params=r)

        try:
            session.commit()
        except Exception:
            # engine 可能 autocommit；commit 失败不一定意味着写入失败，先不影响主流程
            session.rollback()

        return len(rows)

    def aggregate_department_weekly_summary(
        self,
        session: Session,
        week_start: date,
        week_end: date,
        department_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        按 department_id 汇总周指标（可选过滤单个部门）。
        """
        sql = """
        SELECT
            department_id,
            metric,
            data_source,
            SUM(value) AS value
        FROM crm_todos_weekly_metrics
        WHERE week_start = :week_start
          AND week_end = :week_end
        """
        params: dict[str, Any] = {"week_start": week_start, "week_end": week_end}
        if department_id:
            sql += " AND department_id = :department_id"
            params["department_id"] = department_id
        sql += " GROUP BY department_id, metric, data_source ORDER BY department_id, metric, data_source"

        rows = session.exec(text(sql), params=params).fetchall()

        by_dept: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            m = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
            dept = m.get("department_id") or self.DEPARTMENT_UNKNOWN
            by_dept.setdefault(dept, []).append(
                {
                    "metric": m.get("metric"),
                    "data_source": m.get("data_source") or "",
                    "value": int(m.get("value") or 0),
                }
            )

        result: list[dict[str, Any]] = []
        for dept, metrics in by_dept.items():
            result.append(
                {
                    "report_start_date": week_start,
                    "report_end_date": week_end,
                    "department_id": dept,
                    "metrics": metrics,
                }
            )
        return result

    def aggregate_company_weekly_summary(
        self,
        session: Session,
        week_start: date,
        week_end: date,
    ) -> dict[str, Any] | None:
        """
        全公司总计（汇总所有 department_id）。
        """
        rows = session.exec(
            text(
                """
                SELECT
                    metric,
                    data_source,
                    SUM(value) AS value
                FROM crm_todos_weekly_metrics
                WHERE week_start = :week_start
                  AND week_end = :week_end
                GROUP BY metric, data_source
                ORDER BY metric, data_source
                """
            ),
            params={"week_start": week_start, "week_end": week_end},
        ).fetchall()

        if not rows:
            return None

        metrics: list[dict[str, Any]] = []
        for r in rows:
            m = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
            metrics.append(
                {
                    "metric": m.get("metric"),
                    "data_source": m.get("data_source") or "",
                    "value": int(m.get("value") or 0),
                }
            )

        return {
            "report_start_date": week_start,
            "report_end_date": week_end,
            "metrics": metrics,
        }


crm_sales_task_statistics_service = CRMSalesTaskStatisticsService()

