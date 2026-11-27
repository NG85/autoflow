from sqlalchemy import and_, cast, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import TextClause

from app.core.config import DatabaseDialect, settings


def is_postgresql() -> bool:
    return settings.DATABASE_DIALECT == DatabaseDialect.POSTGRESQL


def _ident(name: str) -> str:
    if not name.isidentifier():
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


def quote_ident(name: str) -> str:
    ident = _ident(name)
    if is_postgresql():
        return f'"{ident}"'
    return f"`{ident}`"


def ordered_distinct_agg(column: str) -> str:
    """Distinct ordered text aggregation for one known column.

    TiDB uses ``GROUP_CONCAT``. PostgreSQL uses ``string_agg``.
    """
    quoted = quote_ident(column)
    if is_postgresql():
        return f"string_agg(DISTINCT {quoted}, :sep ORDER BY {quoted}) AS {quoted}"
    return (
        f"GROUP_CONCAT(DISTINCT {quoted} ORDER BY {quoted} SEPARATOR :sep) AS {quoted}"
    )


def raise_group_concat_limit(session) -> None:
    """TiDB/MySQL truncates GROUP_CONCAT unless the session limit is raised."""
    if not is_postgresql():
        session.exec(text("SET SESSION group_concat_max_len = 1048576"))


def upsert_statement(
    table: str,
    columns: list[str],
    conflict: list[str],
    updates: list[str],
) -> TextClause:
    """Insert or update the unique key.

    TiDB uses ``ON DUPLICATE KEY UPDATE`` and ``VALUES(column)``.
    PostgreSQL uses ``ON CONFLICT`` on the same unique columns and ``EXCLUDED``.
    """
    table_sql = quote_ident(table)
    column_names = [_ident(column) for column in columns]
    conflict_names = [_ident(column) for column in conflict]
    update_names = [_ident(column) for column in updates]
    column_sql = ", ".join(quote_ident(column) for column in column_names)
    value_sql = ", ".join(f":{column}" for column in column_names)
    if is_postgresql():
        conflict_sql = ", ".join(quote_ident(column) for column in conflict_names)
        assignment_sql = ", ".join(
            f"{quote_ident(column)} = EXCLUDED.{quote_ident(column)}"
            for column in update_names
        )
        return text(
            f"INSERT INTO {table_sql} ({column_sql}) VALUES ({value_sql}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET "
            f"{assignment_sql}, updated_at = CURRENT_TIMESTAMP"
        )
    assignment_sql = ", ".join(
        f"{quote_ident(column)} = VALUES({quote_ident(column)})"
        for column in update_names
    )
    return text(
        f"INSERT INTO {table_sql} ({column_sql}) VALUES ({value_sql}) "
        f"ON DUPLICATE KEY UPDATE {assignment_sql}, updated_at = CURRENT_TIMESTAMP"
    )


def json_array_is_nonempty(column):
    """True when a JSON column holds a non-empty array."""
    if is_postgresql():
        return and_(
            column.isnot(None),
            func.json_typeof(column) == "array",
            func.json_array_length(column) > 0,
        )
    return and_(column.isnot(None), func.json_length(column) > 0)


def json_object_field_is_array(column, field: str):
    """True when ``column.field`` is a JSON array."""
    _ident(field)
    if is_postgresql():
        return func.jsonb_typeof(cast(column, JSONB)[field]) == "array"
    return func.json_type(func.json_extract(column, f"$.{field}")) == "ARRAY"


def json_array_object_id_equals(column, field: str, value: str):
    """True when a JSON array of objects contains ``id == value``."""
    _ident(field)
    if is_postgresql():
        return cast(column, JSONB)[field].contains([{"id": value}])
    return func.json_search(column, "one", value, None, f"$.{field}[*].id").isnot(None)
