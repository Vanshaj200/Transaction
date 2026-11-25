from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, any_user, writer
from app.schemas.item import ItemCreate, ItemOut, ItemUpdate
from app.services import masters

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", response_model=ItemOut, status_code=201, dependencies=[Depends(writer)])
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    return masters.create_item(db, data)


@router.get("", response_model=list[ItemOut], dependencies=[Depends(any_user)])
def list_items(
    q: Annotated[str | None, Query(description="name contains")] = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    return masters.list_items(db, q, active_only, limit, offset)


@router.get("/{item_id}", response_model=ItemOut, dependencies=[Depends(any_user)])
def get_item(item_id: int, db: Session = Depends(get_db)):
    return masters.get_item(db, item_id)


@router.patch("/{item_id}", response_model=ItemOut, dependencies=[Depends(writer)])
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)):
    return masters.update_item(db, item_id, data)


@router.delete("/{item_id}", status_code=204, dependencies=[Depends(admin_only)])
def deactivate_item(item_id: int, db: Session = Depends(get_db)):
    """Soft delete (items with stock history cannot be removed)."""
    masters.update_item(db, item_id, ItemUpdate(is_active=False))
