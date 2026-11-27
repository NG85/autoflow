"""Tables this service maps for reads but does not create or migrate.

Another service owns the schema. Models stay so queries can run after that
service has created the tables. Alembic must not emit them.
"""

EXTERNAL_TABLES = frozenset(
    {
        "apt_sell_billing",
        "company_competitor",
        "crm_account_assessment",
        "crm_account_opportunity_assessment",
        "crm_accounts",
        "crm_contacts",
        "crm_daily_account_statistics",
        "crm_data_authority",
        "crm_department_daily_summary",
        "crm_entity_insight",
        "crm_forecast_type_mappings",
        "crm_leads",
        "crm_opportunities",
        "crm_orders",
        "crm_payment_plans",
        "crm_postvisit_extract_items",
        "crm_report_index",
        "crm_sales_activities",
        "crm_skill_extract_items",
        "crm_system_configurations",
        "crm_user",
        "department_mirror",
        "oauth_accounts",
        "user_department_relation",
        "user_profiles",
        "user_reporting_relation",
        "users",
        "crm_review_attendee",
        "crm_review_cxo_report",
        "crm_review_department",
        "crm_review_kpi_metric_opp_link",
        "crm_review_kpi_metrics",
        "crm_review_opp_branch_snapshot",
        "crm_review_opp_branch_snapshot_cache",
        "crm_review_opp_risk_progress",
        "crm_review_risk_category",
        "crm_review_risk_opportunity_relation",
        "crm_review_session",
    }
)
