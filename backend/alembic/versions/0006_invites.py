"""Team invites table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22

Adds `invites` for the link-based invite flow:
  founder creates invite → shares link → recipient lands on /accept-invite/<token>
  → creates user under the same org.

Tokens are 32 random-bytes hex (64 chars). We index org_id + email so a
re-invite to the same email replaces (or refuses) cleanly, and token so the
accept page can look up by token directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_invites_org_email", "invites", ["org_id", "email"], unique=False)
    # Partial unique index: at most ONE pending (non-accepted, non-revoked,
    # non-expired) invite per (org, email) at a time.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invites_pending_org_email
        ON invites (org_id, lower(email))
        WHERE accepted_at IS NULL AND revoked_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_invites_pending_org_email")
    op.drop_index("ix_invites_org_email", table_name="invites")
    op.drop_table("invites")
