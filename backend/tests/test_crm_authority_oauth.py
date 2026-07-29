"""RAG get_user_crm_authority：OAuth data-scope 驱动物化单测。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.rag.chat.crm_authority import (
    extract_mirror_crm_ids,
    get_user_crm_authority,
    scope_has_global,
)
from app.rag.types import CrmDataType

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def test_scope_has_global_true():
    assert scope_has_global([{"source": "global", "enabled": True}]) is True


def test_scope_has_global_false():
    assert scope_has_global([{"source": "crm_data_authority", "crmId": "c1"}]) is False


def test_extract_mirror_crm_ids_self_and_org():
    ids = extract_mirror_crm_ids(
        [
            {"source": "crm_data_authority", "crmId": "self"},
            {
                "source": "org_scope",
                "mirrorMatch": True,
                "crmUserIds": ["self", "sub-1"],
            },
        ]
    )
    assert ids == ["self", "sub-1"]


def test_get_user_crm_authority_global_returns_admin():
    account_scope = {
        "entity": "crm_account",
        "merge": "OR",
        "filters": [{"source": "global", "enabled": True}],
    }
    opportunity_scope = {
        "entity": "crm_opportunity",
        "merge": "OR",
        "filters": [{"source": "global", "enabled": True}],
    }

    with patch("app.rag.chat.crm_authority.Session") as session_cls:
        session = MagicMock()
        session_cls.return_value.__enter__.return_value = session
        with patch(
            "app.rag.chat.crm_authority.user_profile_repo.get_crm_user_id_by_user_id",
            return_value="crm-001",
        ):
            with patch(
                "app.rag.chat.crm_authority.oauth_client.get_data_scope",
                side_effect=[account_scope, opportunity_scope],
            ) as get_scope:
                with patch(
                    "app.rag.chat.crm_authority.crm_data_authority_repo.list_authority_rows"
                ) as list_rows:
                    authority, role = get_user_crm_authority(USER_ID)

    assert role == "admin"
    assert authority.is_empty()
    assert get_scope.call_count == 2
    list_rows.assert_not_called()


def test_get_user_crm_authority_materializes_from_oauth_crm_ids():
    account_scope = {
        "entity": "crm_account",
        "merge": "OR",
        "filters": [
            {"source": "crm_data_authority", "crmId": "crm-001"},
            {
                "source": "org_scope",
                "mirrorMatch": True,
                "crmUserIds": ["crm-001", "crm-002"],
            },
        ],
    }
    opportunity_scope = {
        "entity": "crm_opportunity",
        "merge": "OR",
        "filters": [{"source": "crm_data_authority", "crmId": "crm-001"}],
    }

    with patch("app.rag.chat.crm_authority.Session") as session_cls:
        session = MagicMock()
        session_cls.return_value.__enter__.return_value = session
        with patch(
            "app.rag.chat.crm_authority.user_profile_repo.get_crm_user_id_by_user_id",
            return_value="crm-001",
        ):
            with patch(
                "app.rag.chat.crm_authority.oauth_client.get_data_scope",
                side_effect=[account_scope, opportunity_scope],
            ):
                with patch(
                    "app.rag.chat.crm_authority.crm_data_authority_repo.list_authority_rows",
                    return_value=[
                        ("crm_account", "acc-1"),
                        ("crm_opportunity", "opp-1"),
                    ],
                ) as list_rows:
                    authority, role = get_user_crm_authority(USER_ID)

    assert role is None
    assert authority.is_authorized_account("acc-1")
    assert authority.is_authorized_opportunity("opp-1")
    list_rows.assert_called_once()
    kwargs = list_rows.call_args.kwargs
    assert kwargs["crm_ids"] == ["crm-001", "crm-002"]
    assert kwargs["authority_types"] is None


def test_get_user_crm_authority_empty_filters_deny():
    empty_scope = {"entity": "crm_account", "merge": "OR", "filters": []}

    with patch("app.rag.chat.crm_authority.Session") as session_cls:
        session = MagicMock()
        session_cls.return_value.__enter__.return_value = session
        with patch(
            "app.rag.chat.crm_authority.user_profile_repo.get_crm_user_id_by_user_id",
            return_value="crm-001",
        ):
            with patch(
                "app.rag.chat.crm_authority.oauth_client.get_data_scope",
                return_value=empty_scope,
            ):
                with patch(
                    "app.rag.chat.crm_authority.crm_data_authority_repo.list_authority_rows"
                ) as list_rows:
                    authority, role = get_user_crm_authority(USER_ID)

    assert role is None
    assert authority.is_empty()
    list_rows.assert_not_called()


def test_get_user_crm_authority_typed_opportunity_only():
    opportunity_scope = {
        "entity": "crm_opportunity",
        "merge": "OR",
        "filters": [{"source": "crm_data_authority", "crmId": "crm-001"}],
    }

    with patch("app.rag.chat.crm_authority.Session") as session_cls:
        session = MagicMock()
        session_cls.return_value.__enter__.return_value = session
        with patch(
            "app.rag.chat.crm_authority.user_profile_repo.get_crm_user_id_by_user_id",
            return_value="crm-001",
        ):
            with patch(
                "app.rag.chat.crm_authority.oauth_client.get_data_scope",
                return_value=opportunity_scope,
            ) as get_scope:
                with patch(
                    "app.rag.chat.crm_authority.crm_data_authority_repo.list_authority_rows",
                    return_value=[("crm_opportunity", "opp-9")],
                ) as list_rows:
                    authority, role = get_user_crm_authority(
                        USER_ID, crm_type=CrmDataType.OPPORTUNITY
                    )

    assert role is None
    assert get_scope.call_count == 1
    get_scope.assert_called_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        entity="crm_opportunity",
    )
    assert list_rows.call_args.kwargs["authority_types"] == ["crm_opportunity"]
    assert authority.is_authorized_opportunity("opp-9")
