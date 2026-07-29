"""crm_opportunity data-scope → crm_opportunities SQL 翻译测试。"""

from app.permissions.crm_opportunity_scope_translator import (
    translate_crm_opportunity_scope_to_sql,
)


def test_global_allows_all():
    perm = translate_crm_opportunity_scope_to_sql(
        [{"source": "global", "enabled": True}],
        "OR",
    )
    assert perm.sql == "1=1"
    assert perm.params == {}


def test_empty_filters_deny():
    perm = translate_crm_opportunity_scope_to_sql([], "OR")
    assert perm.sql == "1=0"


def test_crm_data_authority_uses_opportunity_unique_id():
    perm = translate_crm_opportunity_scope_to_sql(
        [{"source": "crm_data_authority", "crmId": "crm-001"}],
        "OR",
    )
    assert "crm_opportunities.unique_id" in perm.sql
    assert "crm_data_authority" in perm.sql
    assert perm.params["perm_crm_id_0"] == "crm-001"
    assert perm.params["perm_entity_type"] == "crm_opportunity"


def test_org_scope_mirror_match():
    perm = translate_crm_opportunity_scope_to_sql(
        [
            {
                "source": "org_scope",
                "mirrorMatch": True,
                "crmUserIds": ["crm-a", "crm-b"],
            }
        ],
        "OR",
    )
    assert "crm_opportunities.unique_id" in perm.sql
    assert "d.crm_id IN (" in perm.sql
    assert perm.params["perm_org_id_0_0"] == "crm-a"
    assert perm.params["perm_org_id_0_1"] == "crm-b"
    assert perm.params["perm_entity_type"] == "crm_opportunity"


def test_high_seas_is_ignored_for_opportunity():
    perm = translate_crm_opportunity_scope_to_sql(
        [
            {"source": "crm_data_authority", "crmId": "crm-001"},
            {"source": "high_seas", "enabled": True},
        ],
        "OR",
    )
    assert "person_in_charge_id" not in perm.sql
    assert "high_seas" not in perm.sql.lower()
    assert "crm_data_authority" in perm.sql
    assert perm.params["perm_crm_id_0"] == "crm-001"


def test_high_seas_only_denies():
    perm = translate_crm_opportunity_scope_to_sql(
        [{"source": "high_seas", "enabled": True}],
        "OR",
    )
    assert perm.sql == "1=0"
