"""add doctor recommendation audit records

Revision ID: 7c2d9e41a6b0
Revises: 1a6ce5b49d0e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c2d9e41a6b0"
down_revision: Union[str, None] = "1a6ce5b49d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doctor_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("detection_task_id", sa.Integer(), nullable=False),
        sa.Column("patient_profile_id", sa.Integer(), nullable=True),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("specialty", sa.String(length=200), nullable=True),
        sa.Column("matched_lesions", sa.JSON(), nullable=True),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("context_snapshot", sa.JSON(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("selection_method", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("selected_by", sa.Integer(), nullable=True),
        sa.Column("selected_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["detection_task_id"], ["detection_tasks.id"]),
        sa.ForeignKeyConstraint(["doctor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["patient_profile_id"], ["patient_profiles.id"]),
        sa.ForeignKeyConstraint(["selected_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_doctor_recommendations_detection_task_id"),
        "doctor_recommendations",
        ["detection_task_id"],
    )
    op.create_index(
        op.f("ix_doctor_recommendations_patient_profile_id"),
        "doctor_recommendations",
        ["patient_profile_id"],
    )
    op.create_index(
        op.f("ix_doctor_recommendations_doctor_id"),
        "doctor_recommendations",
        ["doctor_id"],
    )
    op.create_index(
        op.f("ix_doctor_recommendations_created_at"),
        "doctor_recommendations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_doctor_recommendations_created_at"),
        table_name="doctor_recommendations",
    )
    op.drop_index(
        op.f("ix_doctor_recommendations_doctor_id"),
        table_name="doctor_recommendations",
    )
    op.drop_index(
        op.f("ix_doctor_recommendations_patient_profile_id"),
        table_name="doctor_recommendations",
    )
    op.drop_index(
        op.f("ix_doctor_recommendations_detection_task_id"),
        table_name="doctor_recommendations",
    )
    op.drop_table("doctor_recommendations")
