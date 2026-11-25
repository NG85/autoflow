from app.core.config import DatabaseDialect, settings


def vector_column_type(dimension: int):
    """Vector column type for the configured dialect.

    PostgreSQL uses pgvector, so ``cosine_distance`` compiles to ``<=>``.
    TiDB uses ``VEC_COSINE_DISTANCE``. One process loads only one of these.
    """
    if settings.DATABASE_DIALECT == DatabaseDialect.POSTGRESQL:
        from pgvector.sqlalchemy import Vector

        return Vector(dimension)
    from tidb_vector.sqlalchemy import VectorType

    return VectorType(dimension)
