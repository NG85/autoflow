"""Unit tests for short-session branch snapshot_date scoping."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.repositories.crm_review_branch_snapshot import _period_date_filters
from app.services.crm_review_service import CRMReviewService


class _Snap:
    snapshot_period = object()
    snapshot_date = object()


def test_period_date_filters_without_date_is_period_only():
    filters = _period_date_filters(_Snap, snapshot_period="2026-W12", snapshot_date=None)
    assert len(filters) == 1


def test_period_date_filters_with_date_includes_both():
    filters = _period_date_filters(
        _Snap, snapshot_period="2026-W12", snapshot_date=date(2026, 3, 18)
    )
    assert len(filters) == 2


def test_require_branch_snapshot_date_short_session_requires_date():
    session = SimpleNamespace(session_type="lead_analysis", snapshot_date=None)
    with pytest.raises(HTTPException) as exc:
        CRMReviewService._require_branch_snapshot_date(session)
    assert exc.value.status_code == 422


def test_require_branch_snapshot_date_legacy_allows_none():
    session = SimpleNamespace(session_type="legacy_long", snapshot_date=None)
    assert CRMReviewService._require_branch_snapshot_date(session) is None


def test_require_branch_snapshot_date_short_session_ok():
    session = SimpleNamespace(
        session_type="sales_update", snapshot_date=date(2026, 3, 18)
    )
    assert CRMReviewService._require_branch_snapshot_date(session) == date(2026, 3, 18)


def test_append_branch_snapshot_date_filter_noop_when_none():
    where = []
    CRMReviewService._append_branch_snapshot_date_filter(where, _Snap, None)
    assert where == []


def test_append_branch_snapshot_date_filter_adds_clause():
    where = []
    CRMReviewService._append_branch_snapshot_date_filter(
        where, _Snap, date(2026, 3, 18)
    )
    assert len(where) == 1
