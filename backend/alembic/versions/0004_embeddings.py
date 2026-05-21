"""pgvector extension + embedding columns (Tier-2 foundation).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21

Enables the pgvector extension on the database (Neon supports it natively —
free, just run CREATE EXTENSION). Adds nullable `description_embedding`
columns to bank_transactions and receipts so we can store 384-dim sentence
embeddings.

The actual embedding write + similarity search lives in services/embeddings.py
(skeleton today, full wiring next session).

NOTE: This migration is safe to run on Neon. If you ever swap to a Postgres
without pgvector available, the CREATE EXTENSION line will fail — guard with
a DO block that checks pg_available_extensions first.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guard CREATE EXTENSION so the migration doesn't break on databases
    # that don't have pgvector packaged. Neon always does.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_available_extensions WHERE name = 'vector'
            ) THEN
                CREATE EXTENSION IF NOT EXISTS vector;
            ELSE
                RAISE NOTICE 'pgvector not available — embedding columns will be NULL until provisioned';
            END IF;
        END
        $$;
        """
    )

    # Only attempt to add the vector columns if the extension exists.
    # (If the extension wasn't installed, the column type 'vector' would fail.)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                ALTER TABLE bank_transactions
                    ADD COLUMN IF NOT EXISTS description_embedding vector(384);
                ALTER TABLE receipts
                    ADD COLUMN IF NOT EXISTS description_embedding vector(384);
                -- IVFFlat index for fast cosine similarity on bank txns.
                -- Lists=100 is a fine default for up to ~1M rows.
                CREATE INDEX IF NOT EXISTS ix_bank_txns_embedding
                    ON bank_transactions
                    USING ivfflat (description_embedding vector_cosine_ops)
                    WITH (lists = 100);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                DROP INDEX IF EXISTS ix_bank_txns_embedding;
                ALTER TABLE bank_transactions DROP COLUMN IF EXISTS description_embedding;
                ALTER TABLE receipts DROP COLUMN IF EXISTS description_embedding;
            END IF;
        END
        $$;
        """
    )
    # We intentionally do NOT drop the vector extension on downgrade — other
    # tables may use it.
