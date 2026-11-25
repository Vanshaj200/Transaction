from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, any_user, writer
from app.schemas.party import PartyCreate, PartyOut, PartyType, PartyUpdate
from app.services import masters

router = APIRouter(prefix="/parties", tags=["parties"])


@router.post("", response_model=PartyOut, status_code=201, dependencies=[Depends(writer)])
def create_party(data: PartyCreate, db: Session = Depends(get_db)):
    return masters.create_party(db, data)


@router.get("", response_model=list[PartyOut], dependencies=[Depends(any_user)])
def list_parties(
    party_type: PartyType | None = None,
    q: Annotated[str | None, Query(description="name contains")] = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    return masters.list_parties(db, party_type, q, active_only, limit, offset)


@router.get("/{party_id}", response_model=PartyOut, dependencies=[Depends(any_user)])
def get_party(party_id: int, db: Session = Depends(get_db)):
    return masters.get_party(db, party_id)


@router.patch("/{party_id}", response_model=PartyOut, dependencies=[Depends(writer)])
def update_party(party_id: int, data: PartyUpdate, db: Session = Depends(get_db)):
    return masters.update_party(db, party_id, data)


@router.delete("/{party_id}", status_code=204, dependencies=[Depends(admin_only)])
def deactivate_party(party_id: int, db: Session = Depends(get_db)):
    """Soft delete: parties with invoices can never be removed, so we just mark them inactive."""
    masters.update_party(db, party_id, PartyUpdate(is_active=False))
