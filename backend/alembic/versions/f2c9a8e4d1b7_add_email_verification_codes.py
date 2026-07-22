"""add email verification codes

Revision ID: f2c9a8e4d1b7
Revises: 8e41b92c3d77
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c9a8e4d1b7"
down_revision: Union[str, None] = "8e41b92c3d77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("request_ip", sa.String(length=50), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_verification_codes_email",
        "email_verification_codes",
        ["email"],
    )
    op.create_index(
        "ix_email_verification_codes_purpose",
        "email_verification_codes",
        ["purpose"],
    )
    op.create_index(
        "ix_email_verification_codes_request_ip",
        "email_verification_codes",
        ["request_ip"],
    )
    op.create_index(
        "ix_email_verification_codes_expires_at",
        "email_verification_codes",
        ["expires_at"],
    )
    op.create_index(
        "ix_email_verification_codes_created_at",
        "email_verification_codes",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verification_codes_created_at",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_expires_at",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_request_ip",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_purpose",
        table_name="email_verification_codes",
    )
    op.drop_index(
        "ix_email_verification_codes_email",
        table_name="email_verification_codes",
    )
    op.drop_table("email_verification_codes")
