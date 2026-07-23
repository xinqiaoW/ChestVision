"""empty message

Revision ID: b4a45f016662
Revises: 9c0f2d4b6e7a
Create Date: 2026-07-18 10:45:49.326024

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4a45f016662'
down_revision: Union[str, None] = '9c0f2d4b6e7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
