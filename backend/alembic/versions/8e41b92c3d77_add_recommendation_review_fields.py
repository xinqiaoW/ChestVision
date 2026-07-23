"""add doctor recommendation review fields

Revision ID: 8e41b92c3d77
Revises: 7c2d9e41a6b0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8e41b92c3d77"
down_revision: Union[str, None] = "7c2d9e41a6b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "doctor_recommendations",
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "doctor_recommendations",
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "doctor_recommendations",
        sa.Column("review_note", sa.String(length=500), nullable=True),
    )
    op.create_foreign_key(
        "fk_doctor_recommendations_confirmed_by_users",
        "doctor_recommendations",
        "users",
        ["confirmed_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_doctor_recommendations_confirmed_by_users",
        "doctor_recommendations",
        type_="foreignkey",
    )
    op.drop_column("doctor_recommendations", "review_note")
    op.drop_column("doctor_recommendations", "confirmed_at")
    op.drop_column("doctor_recommendations", "confirmed_by")
