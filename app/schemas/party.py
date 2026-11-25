from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.gstin import STATE_CODES, is_valid_gstin

PartyType = Literal["customer", "supplier"]


def _check_state(v: str) -> str:
    if v not in STATE_CODES:
        raise ValueError(f"unknown GST state code {v!r}")
    return v


class PartyBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    party_type: PartyType
    gstin: str | None = Field(default=None, description="15-char GSTIN; omit for unregistered parties")
    state_code: str = Field(description="2-digit GST state code, e.g. 27 = Maharashtra")
    address: str | None = None

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.upper()
        if not is_valid_gstin(v):
            raise ValueError("invalid GSTIN (format or check digit)")
        return v

    @field_validator("state_code")
    @classmethod
    def _state(cls, v: str) -> str:
        return _check_state(v)

    @model_validator(mode="after")
    def _gstin_matches_state(self) -> Self:
        # The first two digits of a GSTIN are the state code of the registration.
        if self.gstin and self.gstin[:2] != self.state_code:
            raise ValueError("GSTIN state prefix does not match state_code")
        return self


class PartyCreate(PartyBase):
    pass


class PartyUpdate(BaseModel):
    """Partial update (PATCH). party_type cannot change: it would orphan invoice semantics."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    gstin: str | None = None
    state_code: str | None = None
    address: str | None = None
    is_active: bool | None = None

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v):
        if v is not None and not is_valid_gstin(v.upper()):
            raise ValueError("invalid GSTIN (format or check digit)")
        return v.upper() if v else v

    @field_validator("state_code")
    @classmethod
    def _state(cls, v):
        return _check_state(v) if v is not None else v


class PartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    party_type: PartyType
    gstin: str | None
    state_code: str
    address: str | None
    is_active: bool
