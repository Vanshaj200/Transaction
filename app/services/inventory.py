"""Stock handling. Every change goes through change_stock() so that items.stock_qty and the
stock_movements ledger can never drift apart."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import InsufficientStock, NotFound
from app.models import Item, StockMovement


def lock_items(db: Session, item_ids: Iterable[int]) -> dict[int, Item]:
    """SELECT ... FOR UPDATE the given items and return them by id.

    * Locks are taken in ascending id order. Two requests that both need items 3 and 7 therefore
      both lock 3 first, so they queue up instead of deadlocking (A holds 3 wants 7, B holds 7 wants 3).
    * populate_existing makes the ORM overwrite any copy of the row it already holds with the
      freshly locked, latest committed values. Without it the stock check could use a stale number.
    * Other transactions that want these rows block here until we commit/rollback.
    """
    ids = sorted(set(item_ids))
    stmt = (
        select(Item).where(Item.id.in_(ids)).order_by(Item.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    items = {item.id: item for item in db.scalars(stmt)}
    missing = [i for i in ids if i not in items]
    if missing:
        raise NotFound(f"Item(s) not found: {missing}")
    return items


def change_stock(db: Session, item: Item, delta: int, reason: str, invoice_id: int | None) -> None:
    """Apply a stock change to an item that is ALREADY LOCKED by this transaction.
    Refuses to take stock below zero (the DB CHECK stock_qty >= 0 is the second safety net)."""
    new_qty = item.stock_qty + delta
    if new_qty < 0:
        raise InsufficientStock(item.id, item.name, available=item.stock_qty, requested=-delta)
    item.stock_qty = new_qty
    db.add(StockMovement(item_id=item.id, qty_change=delta, reason=reason, invoice_id=invoice_id))
