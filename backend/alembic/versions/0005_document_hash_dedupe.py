"""Document content hash for upload-time deduplication.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22

Adds:
  - documents.content_sha256 — hex digest of the raw file bytes; lets us
    reject re-uploads of the same physical file with HTTP 409.
  - documents.deleted_at — soft-delete column. Duplicates surfaced by the
    review queue are flagged here so the dashboard ignores them without
    losing the audit trail.

Constraints:
  - Unique partial index on (org_id, content_sha256) WHERE content_sha256
    IS NOT NULL — old rows without a hash don't block new inserts but new
    rows with matching hash will conflict cleanly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial unique index — only enforces uniqueness on rows that have a
    # hash. Backfill can fill in old rows lazily without tripping the index.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_org_sha256
        ON documents (org_id, content_sha256)
        WHERE content_sha256 IS NOT NULL AND deleted_at IS NULL
        """
    )
    op.create_index(
        "ix_documents_deleted_at",
        "documents",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_deleted_at", table_name="documents")
    op.execute("DROP INDEX IF EXISTS uq_documents_org_sha256")
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "content_sha256")
