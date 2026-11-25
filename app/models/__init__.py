"""Import every model so Base.metadata knows all tables (Alembic autogenerate relies on this)."""
from app.models.account import Account
from app.models.base import Base
from app.models.invoice import Invoice, InvoiceLine
from app.models.item import Item
from app.models.journal import JournalEntry, JournalLine
from app.models.party import Party
from app.models.payment import Payment
from app.models.stock import StockMovement
from app.models.user import User

__all__ = [
    "Account", "Base", "Invoice", "InvoiceLine", "Item", "JournalEntry",
    "JournalLine", "Party", "Payment", "StockMovement", "User",
]
