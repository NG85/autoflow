"""推送场景目录。"""

from app.services.notification_scene_catalog import (
    PREF_ELIGIBLE_OPT_OUT,
    PREF_NONE,
    ROUTING_ELIGIBLE_SET,
    ROUTING_INSTANCE,
    SCENE_COMPANY_DAILY,
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_DAILY,
    SCENE_DEPARTMENT_HIGHLIGHTS,
    SCENE_SALES_DAILY,
    SCENE_VISIT_RECORD,
    VARIANT_KPI_CARD,
    VARIANT_SUMMARY_MD,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_VISIT_CARD,
    VARIANT_VISIT_REPORT,
    get_scene,
    normalize_scene,
    scene_allows_variant,
)


def test_visit_record_is_instance_routing_without_preference():
    spec = get_scene(SCENE_VISIT_RECORD)
    assert spec.routing == ROUTING_INSTANCE
    assert spec.preference == PREF_NONE
    assert spec.requires_record_id is True


def test_company_and_department_reports_are_eligible_opt_out():
    company = get_scene(SCENE_COMPANY_DAILY)
    dept = get_scene(SCENE_DEPARTMENT_DAILY)
    assert company.routing == ROUTING_ELIGIBLE_SET
    assert company.preference == PREF_ELIGIBLE_OPT_OUT
    assert dept.routing == ROUTING_ELIGIBLE_SET
    assert dept.preference == PREF_ELIGIBLE_OPT_OUT
    assert dept.requires_department is True
    assert dept.includes_groups is True


def test_visit_report_variant_only_on_weekly_slots():
    assert scene_allows_variant(SCENE_COMPANY_WEEKLY, VARIANT_VISIT_REPORT)
    assert not scene_allows_variant(SCENE_COMPANY_DAILY, VARIANT_VISIT_REPORT)
    assert scene_allows_variant(SCENE_COMPANY_DAILY, VARIANT_KPI_CARD)
    assert scene_allows_variant(SCENE_COMPANY_DAILY, VARIANT_SUMMARY_MD)
    assert scene_allows_variant(SCENE_DEPARTMENT_DAILY, VARIANT_SUMMARY_MD)
    assert not scene_allows_variant(SCENE_COMPANY_WEEKLY, VARIANT_SUMMARY_MD)
    assert scene_allows_variant(SCENE_VISIT_RECORD, VARIANT_VISIT_CARD)
    assert not scene_allows_variant(SCENE_VISIT_RECORD, VARIANT_KPI_CARD)
    assert not scene_allows_variant(SCENE_COMPANY_DAILY, VARIANT_VISIT_CARD)


def test_today_highlights_is_sales_variant_and_independent_company_department_scenes():
    assert scene_allows_variant(SCENE_SALES_DAILY, VARIANT_TODAY_HIGHLIGHTS)
    assert not scene_allows_variant(SCENE_COMPANY_DAILY, VARIANT_TODAY_HIGHLIGHTS)
    assert not scene_allows_variant(SCENE_DEPARTMENT_DAILY, VARIANT_TODAY_HIGHLIGHTS)
    assert not scene_allows_variant(SCENE_COMPANY_WEEKLY, VARIANT_TODAY_HIGHLIGHTS)

    company = get_scene(SCENE_COMPANY_HIGHLIGHTS)
    assert company.routing == ROUTING_ELIGIBLE_SET
    assert company.preference == PREF_ELIGIBLE_OPT_OUT
    assert company.requires_department is False
    assert company.includes_groups is False
    assert scene_allows_variant(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS)
    assert not scene_allows_variant(SCENE_COMPANY_HIGHLIGHTS, VARIANT_KPI_CARD)

    dept = get_scene(SCENE_DEPARTMENT_HIGHLIGHTS)
    assert dept.routing == ROUTING_ELIGIBLE_SET
    assert dept.preference == PREF_ELIGIBLE_OPT_OUT
    assert dept.requires_department is True
    assert dept.includes_groups is False
    assert scene_allows_variant(SCENE_DEPARTMENT_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS)


def test_normalize_legacy_report_slot_aliases():
    assert SCENE_SALES_DAILY == "sales_daily"
    assert normalize_scene("sales_daily_report") == SCENE_SALES_DAILY
    assert normalize_scene("company_weekly_report") == SCENE_COMPANY_WEEKLY
    assert normalize_scene("department_daily_report") == SCENE_DEPARTMENT_DAILY
