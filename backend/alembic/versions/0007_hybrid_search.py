"""Hybrid search — tsvector columns + GIN indexes for BM25-style keyword retrieval.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22

Adds a `description_tsv` tsvector column (Postgres generated) to:
  - bank_transactions  → indexes the description text
  - invoices           → indexes invoice_number for exact-match wins
  - receipts           → indexes notes
  - vendors            → indexes vendor name + aliases

Each backed by a GIN index for sub-millisecond keyword search.

These columns are GENERATED ALWAYS STORED so they update automatically
whenever the source text changes — no application-level sync needed.
ts_rank_cd() gives us BM25-flavored relevance scoring over them, which
we fuse with the existing pgvector cosine distance for the hybrid leg.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bank_transactions: index the description column
    op.execute(
        """
        ALTER TABLE bank_transactions
        ADD COLUMN IF NOT EXISTS description_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(description, ''))) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_bank_txn_description_tsv
        ON bank_transactions USING GIN(description_tsv)
        """
    )

    # invoices: index invoice_number (rich text gets it from the related vendor
    # name via join at query time, not via a column here)
    op.execute(
        """
        ALTER TABLE invoices
        ADD COLUMN IF NOT EXISTS number_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(invoice_number, ''))) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_invoices_number_tsv
        ON invoices USING GIN(number_tsv)
        """
    )

    # receipts: index notes
    op.execute(
        """
        ALTER TABLE receipts
        ADD COLUMN IF NOT EXISTS notes_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(notes, ''))) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_receipts_notes_tsv
        ON receipts USING GIN(notes_tsv)
        """
    )

    # vendors: index name + aliases concatenated.  aliases is a text[]; flatten
    # it via array_to_string for the index.
    #
    # Postgres rejects the inline form because it considers
    # `array_to_string(text[], text)` in concatenation context as non-
    # immutable for the purposes of a GENERATED STORED column. The fix is
    # to wrap the expression in a SQL function we explicitly declare
    # IMMUTABLE — Postgres trusts the declaration. The body is genuinely
    # immutable (same inputs → same output, no I/O, no clock), so the
    # declaration is honest, not a lie.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vendor_search_text(p_name text, p_aliases text[])
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        AS $$
          SELECT coalesce(p_name, '') || ' ' ||
                 coalesce(array_to_string(p_aliases, ' '), '');
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE vendors
        ADD COLUMN IF NOT EXISTS search_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', vendor_search_text(name, aliases))
        ) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_vendors_search_tsv
        ON vendors USING GIN(search_tsv)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vendors_search_tsv")
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS search_tsv")
    op.execute("DROP FUNCTION IF EXISTS vendor_search_text(text, text[])")
    op.execute("DROP INDEX IF EXISTS ix_receipts_notes_tsv")
    op.execute("ALTER TABLE receipts DROP COLUMN IF EXISTS notes_tsv")
    op.execute("DROP INDEX IF EXISTS ix_invoices_number_tsv")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS number_tsv")
    op.execute("DROP INDEX IF EXISTS ix_bank_txn_description_tsv")
    op.execute("ALTER TABLE bank_transactions DROP COLUMN IF EXISTS description_tsv")
