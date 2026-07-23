"""add doctor assignment requests

Revision ID: 9c90953099b3
Revises: 29031deaaa74
Create Date: 2026-07-23 14:51:32.899108

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9c90953099b3"
down_revision: Union[str, None] = "29031deaaa74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doctor_assignment_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False, comment="患者用户ID"),
        sa.Column("doctor_id", sa.Integer(), nullable=False, comment="医生用户ID"),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=True,
            comment="pending / approved / rejected",
        ),
        sa.Column(
            "request_source",
            sa.String(length=30),
            nullable=True,
            comment="来源：recommendation（AI推荐）/ manual（医生选择页）",
        ),
        sa.Column(
            "detection_task_id",
            sa.Integer(),
            nullable=True,
            comment="关联检测任务（推荐来源时）",
        ),
        sa.Column("requested_by", sa.Integer(), nullable=False, comment="请求发起者"),
        sa.Column("reviewed_by", sa.Integer(), nullable=True, comment="审批管理员"),
        sa.Column(
            "review_note", sa.String(length=500), nullable=True, comment="审批备注"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True, comment="请求时间"),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True, comment="审批时间"),
        sa.ForeignKeyConstraint(
            ["detection_task_id"],
            ["detection_tasks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_doctor_assignment_requests_doctor_id"),
        "doctor_assignment_requests",
        ["doctor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_doctor_assignment_requests_patient_id"),
        "doctor_assignment_requests",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_doctor_assignment_requests_patient_id"),
        table_name="doctor_assignment_requests",
    )
    op.drop_index(
        op.f("ix_doctor_assignment_requests_doctor_id"),
        table_name="doctor_assignment_requests",
    )
    op.drop_table("doctor_assignment_requests")
    # ### end Alembic commands ###
