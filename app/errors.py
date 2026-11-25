"""Domain errors. Services raise these; main.py maps them to HTTP responses."""


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    """State conflict: e.g. cancelling twice, insufficient stock, paying a cancelled invoice."""
    status_code = 409


class InvalidInput(DomainError):
    status_code = 422


class InsufficientStock(Conflict):
    def __init__(self, item_id: int, name: str, available: int, requested: int):
        super().__init__(
            f"Insufficient stock for item {item_id} ({name}): available {available}, requested {requested}"
        )
        self.item_id, self.available, self.requested = item_id, available, requested




class LedgerError(DomainError):
    """Accounting integrity problem (missing account, bad posting). Never the client's fault."""
    status_code = 500


class UnbalancedEntry(LedgerError):
    """A journal entry whose debits != credits. This is a programming error, so it is a 500."""
