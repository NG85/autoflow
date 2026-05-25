from unittest.mock import MagicMock

from app.repositories.department_mirror import DepartmentMirrorRepo


def _mock_session(rows):
    session = MagicMock()
    session.exec.return_value.all.return_value = rows
    return session


def test_ancestor_chain_includes_mirror_root():
    session = _mock_session(
        [
            ("child", "root", "子部门"),
            ("root", None, "根部门"),
        ]
    )
    result = DepartmentMirrorRepo().get_ancestor_chains_bulk(session, ["child"])
    assert result == {"child": [("child", "子部门"), ("root", "根部门")]}


def test_ancestor_chain_truncates_when_parent_missing_from_mirror():
    session = _mock_session(
        [
            ("child", "root-missing", "子部门"),
        ]
    )
    result = DepartmentMirrorRepo().get_ancestor_chains_bulk(session, ["child"])
    assert result == {"child": [("child", "子部门")]}


def test_ancestor_chain_unknown_when_start_not_in_mirror():
    session = _mock_session(
        [
            ("child", "root", "子部门"),
        ]
    )
    result = DepartmentMirrorRepo().get_ancestor_chains_bulk(session, ["ghost"])
    assert result == {"ghost": [("ghost", "未知部门")]}
