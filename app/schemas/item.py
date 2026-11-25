from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.gst import ALLOWED_RATES_BP


def _check_rate(v: int) -> int:
    if v not in ALLOWED_RATES_BP:
        raise ValueError(f"gst_rate_bp must be one of {ALLOWED_RATES_BP} (basis points; 1800 = 18%)")
    return v


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    hsn: str = Field(pattern=r"^\d{4}(\d{2})?(\d{2})?$", description="HSN code, 4/6/8 digits")
    gst_rate_bp: int = Field(description="GST rate in basis points: 1800 = 18%")
    unit: str = Field(default="NOS", max_length=10)
    selling_price: int = Field(ge=0, description="Price per unit in PAISE (Rs 100.00 = 10000)")
    opening_stock: int = Field(default=0, ge=0, description="Units on hand at creation")

    _rate = field_validator("gst_rate_bp")(_check_rate)


class ItemUpdate(BaseModel):
    """Stock is NOT editable here: it only changes through invoices (and the opening movement)."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    hsn: str | None = Field(default=None, pattern=r"^\d{4}(\d{2})?(\d{2})?$")
    gst_rate_bp: int | None = None
    unit: str | None = Field(default=None, max_length=10)
    selling_price: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @field_validator("gst_rate_bp")
    @classmethod
    def _rate(cls, v):
        return _check_rate(v) if v is not None else v


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    hsn: str
    gst_rate_bp: int
    unit: str
    selling_price: int
    stock_qty: int
    is_active: bool
