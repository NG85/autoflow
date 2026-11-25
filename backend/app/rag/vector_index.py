from sqlalchemy import text
from sqlalchemy.sql.schema import Column

from app.core.config import DatabaseDialect, settings
from app.core.db import engine


def create_cosine_hnsw_index(column: Column) -> None:
    """Create a cosine HNSW index for one vector column.

    TiDB keeps VectorAdaptor. PostgreSQL uses pgvector ``vector_cosine_ops``.
    Index names include the table because PostgreSQL names are schema-global,
    and ``description_vec`` exists on both entity and relationship tables.
    """
    if settings.DATABASE_DIALECT != DatabaseDialect.POSTGRESQL:
        import tidb_vector
        from tidb_vector.sqlalchemy import VectorAdaptor

        VectorAdaptor(engine).create_vector_index(
            column, tidb_vector.DistanceMetric.COSINE
        )
        return

    index_name = f"{column.table.name}_{column.name}_hnsw"[:63]
    preparer = engine.dialect.identifier_preparer
    statement = text(
        f"CREATE INDEX IF NOT EXISTS {preparer.quote(index_name)} "
        f"ON {preparer.quote(column.table.name)} "
        f"USING hnsw ({preparer.quote(column.name)} vector_cosine_ops)"
    )
    with engine.begin() as conn:
        conn.execute(statement)
