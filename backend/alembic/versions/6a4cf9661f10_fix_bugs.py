"""fix bugs

Revision ID: 6a4cf9661f10
Revises: b4a45f016662
Create Date: 2026-07-18 10:54:16.080445

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a4cf9661f10'
down_revision: Union[str, None] = 'b4a45f016662'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
