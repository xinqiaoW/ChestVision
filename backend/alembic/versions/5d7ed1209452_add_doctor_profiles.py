"""add doctor profiles

Revision ID: 5d7ed1209452
Revises: f2c9a8e4d1b7
Create Date: 2026-07-23 11:00:16.240315

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5d7ed1209452"
down_revision: Union[str, None] = "f2c9a8e4d1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "doctor_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="关联的用户账号"),
        sa.Column(
            "display_name",
            sa.String(length=50),
            nullable=False,
            comment="医生真实姓名（用于推荐展示）",
        ),
        sa.Column(
            "specialty",
            sa.String(length=200),
            nullable=True,
            comment="专业方向，如'胸部影像诊断'",
        ),
        sa.Column(
            "department", sa.String(length=100), nullable=True, comment="所在科室"
        ),
        sa.Column(
            "title", sa.String(length=50), nullable=True, comment="职称，如'主任医师'"
        ),
        sa.Column("hospital", sa.String(length=100), nullable=True, comment="所在医院"),
        sa.Column("introduction", sa.Text(), nullable=True, comment="个人简介/自述"),
        sa.Column(
            "consultation_hours",
            sa.String(length=200),
            nullable=True,
            comment="出诊时间",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=True, comment="是否启用"),
        sa.Column("created_at", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_doctor_profiles_user_id"), "doctor_profiles", ["user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_doctor_profiles_user_id"), table_name="doctor_profiles")
    op.drop_table("doctor_profiles")
