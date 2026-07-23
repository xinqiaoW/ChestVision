"""merge dev2 and dev migration branches

Revision ID: 29031deaaa74
Revises: 5d7ed1209452, 87436f6d742e
Create Date: 2026-07-23 11:50:40.628137

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29031deaaa74'
down_revision: Union[str, None] = ('5d7ed1209452', '87436f6d742e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
