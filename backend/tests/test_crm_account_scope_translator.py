"""crm_account data-scope → local_contacts SQL 翻译测试。"""

from app.permissions.crm_account_scope_translator import translate_crm_account_scope_to_sql


def test_global_allows_all():
    perm = translate_crm_account_scope_to_sql(
        [{"source": "global", "enabled": True}],
        "OR",
    )
    assert perm.sql == "1=1"
    assert perm.params == {}


def test_empty_filters_deny():
    perm = translate_crm_account_scope_to_sql([], "OR")
    assert perm.sql == "1=0"


def test_crm_data_authority_uses_customer_id():
    perm = translate_crm_account_scope_to_sql(
        [{"source": "crm_data_authority", "crmId": "crm-001"}],
        "OR",
    )
    assert "local_contacts.customer_id" in perm.sql
    assert "crm_data_authority" in perm.sql
    assert perm.params["perm_crm_id_0"] == "crm-001"
    assert perm.params["perm_entity_type"] == "crm_account"


def test_high_seas_joins_crm_accounts():
    perm = translate_crm_account_scope_to_sql(
        [
            {"source": "crm_data_authority", "crmId": "crm-001"},
            {"source": "high_seas", "enabled": True},
        ],
        "OR",
    )
    assert "crm_accounts" in perm.sql
    assert "person_in_charge_id IS NULL" in perm.sql
