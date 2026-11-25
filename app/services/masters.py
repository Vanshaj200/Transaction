"""Create / update parties and items."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound
from app.models import Item, Party, StockMovement
from app.schemas.item import ItemCreate, ItemUpdate
from app.schemas.party import PartyCreate, PartyUpdate


def get_party(db: Session, party_id: int) -> Party:
    party = db.get(Party, party_id)
    if party is None:
        raise NotFound(f"Party {party_id} not found")
    return party


def create_party(db: Session, data: PartyCreate) -> Party:
    party = Party(**data.model_dump())
    db.add(party)
    _commit_unique(db, "A party with this GSTIN already exists")
    return party


def update_party(db: Session, party_id: int, data: PartyUpdate) -> Party:
    party = get_party(db, party_id)
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(party, key, value)
    # Re-check the GSTIN/state consistency that the create schema enforces.
    if party.gstin and party.gstin[:2] != party.state_code:
        raise Conflict("GSTIN state prefix does not match state_code")
    _commit_unique(db, "A party with this GSTIN already exists")
    return party


def get_item(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise NotFound(f"Item {item_id} not found")
    return item


def create_item(db: Session, data: ItemCreate) -> Item:
    """Opening stock is recorded as a stock movement so that
    items.stock_qty == SUM(stock_movements.qty_change) holds from the very first row."""
    item = Item(**data.model_dump(exclude={"opening_stock"}), stock_qty=data.opening_stock)
    db.add(item)
    db.flush()
    if data.opening_stock:
        db.add(StockMovement(item_id=item.id, qty_change=data.opening_stock, reason="opening"))
    db.commit()
    return item


def update_item(db: Session, item_id: int, data: ItemUpdate) -> Item:
    item = get_item(db, item_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return item


def _commit_unique(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(message) from exc


def list_parties(db: Session, party_type, q, active_only, limit, offset) -> list[Party]:
    stmt = select(Party).order_by(Party.name, Party.id).limit(limit).offset(offset)
    if party_type:
        stmt = stmt.where(Party.party_type == party_type)
    if q:
        stmt = stmt.where(Party.name.ilike(f"%{q}%"))
    if active_only:
        stmt = stmt.where(Party.is_active.is_(True))
    return list(db.scalars(stmt))


def list_items(db: Session, q, active_only, limit, offset) -> list[Item]:
    stmt = select(Item).order_by(Item.name, Item.id).limit(limit).offset(offset)
    if q:
        stmt = stmt.where(Item.name.ilike(f"%{q}%"))
    if active_only:
        stmt = stmt.where(Item.is_active.is_(True))
    return list(db.scalars(stmt))
