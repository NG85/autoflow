"""int_enum_type

Revision ID: 2adc0b597dcd
Revises: a54f966436ce
Create Date: 2025-01-24 17:58:08.339090

"""

from alembic import op
from sqlalchemy.dialects import mysql

from app.models.base import IntEnumType
from app.models.chat import ChatVisibility

# revision identifiers, used by Alembic.
revision = "2adc0b597dcd"
down_revision = "313b78f69ef0"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "chats",
        "visibility",
        existing_type=mysql.SMALLINT(),
        type_=IntEnumType(ChatVisibility),
        existing_nullable=False,
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "chats",
        "visibility",
        existing_type=IntEnumType(ChatVisibility),
        type_=mysql.SMALLINT(),
        existing_nullable=False,
    )
    # ### end Alembic commands ###
