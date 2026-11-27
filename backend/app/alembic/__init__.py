"""Alembic environment package.

Importing this package selects the migration tree for DATABASE_DIALECT.
Alembic loads this module while resolving ``script_location``, before it
scans revision files.
"""

from app.core.config import settings

__all__ = ["settings"]
