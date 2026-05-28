"""Entity management — legal entities under an organization.

An "organization" in Nira is the customer account; an "entity" is a legal
entity that organization owns. Most users have one; mid-market groups
typically have 2-4 (operating co + investment vehicle + family LLP +
sometimes a property holdco).

Migration 0008 auto-creates one entity per organization on upgrade, so
existing single-tenant operation works without explicit setup. New
entities can be added later through the API.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import Entity

logger = logging.getLogger(__name__)


def get_default_entity(db: Session, org_id: uuid.UUID) -> Entity:
    """Return the default entity for an org. Falls back to creating one if
    none exists (shouldn't happen post-migration, but defensive).

    "Default" today = the first entity by created_at. Once multi-entity
    UI ships, users will pick a default explicitly per org.
    """
    row = db.execute(
        select(Entity)
        .where(Entity.org_id == org_id, Entity.is_active.is_(True))
        .order_by(Entity.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    if row is not None:
        return row

    # Defensive auto-create — migration should have done this already
    logger.warning("No entity found for org %s; auto-creating", org_id)
    from common.models import Organization

    org = db.get(Organization, org_id)
    if org is None:
        raise ValueError(f"Organization {org_id} not found")
    entity = Entity(
        org_id=org_id,
        legal_name=org.name,
        short_name=org.name,
        base_currency="INR",
        country_code="IN",
        financial_year_start_month=4,
        is_active=True,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


def list_entities(db: Session, org_id: uuid.UUID) -> list[Entity]:
    return list(
        db.execute(
            select(Entity)
            .where(Entity.org_id == org_id)
            .order_by(Entity.created_at.asc())
        ).scalars()
    )


def get_entity(
    db: Session, org_id: uuid.UUID, entity_id: uuid.UUID
) -> Optional[Entity]:
    """Get an entity by id, scoped by org. Returns None if not found OR
    if the entity belongs to a different org (security)."""
    row = db.get(Entity, entity_id)
    if row is None or row.org_id != org_id:
        return None
    return row


def resolve_entity(
    db: Session, org_id: uuid.UUID, entity_id: Optional[uuid.UUID]
) -> Entity:
    """If entity_id is provided, return that entity (org-scoped).
    Otherwise return the default entity. Raises if nothing resolves.
    """
    if entity_id is not None:
        e = get_entity(db, org_id, entity_id)
        if e is None:
            raise ValueError(f"Entity {entity_id} not found in org {org_id}")
        return e
    return get_default_entity(db, org_id)


def create_entity(
    db: Session,
    org_id: uuid.UUID,
    legal_name: str,
    short_name: Optional[str] = None,
    pan: Optional[str] = None,
    gstin: Optional[str] = None,
    registration_number: Optional[str] = None,
    base_currency: str = "INR",
    country_code: str = "IN",
    financial_year_start_month: int = 4,
    parent_entity_id: Optional[uuid.UUID] = None,
    commit: bool = True,
) -> Entity:
    """Create a new entity. If parent_entity_id is set, validates the
    parent belongs to the same org (no cross-tenant group structures).
    """
    if parent_entity_id is not None:
        parent = get_entity(db, org_id, parent_entity_id)
        if parent is None:
            raise ValueError(f"Parent entity {parent_entity_id} not in org {org_id}")
    entity = Entity(
        org_id=org_id,
        legal_name=legal_name,
        short_name=short_name or legal_name,
        pan=pan,
        gstin=gstin,
        registration_number=registration_number,
        base_currency=base_currency,
        country_code=country_code,
        financial_year_start_month=financial_year_start_month,
        parent_entity_id=parent_entity_id,
        is_active=True,
    )
    db.add(entity)
    if commit:
        db.commit()
        db.refresh(entity)
    else:
        db.flush()
    return entity
