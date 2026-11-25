"""Print the headline size numbers of the project (counted from the code, not typed by hand)."""
from sqlalchemy import CheckConstraint

from app.main import app
from app.models import Base


def main() -> None:
    paths = app.openapi()["paths"]
    endpoints = sorted(f"{m.upper()} {p}" for p, ops in paths.items() for m in ops)
    tables = Base.metadata.tables
    explicit_indexes = sum(len(t.indexes) for t in tables.values())
    checks = sum(1 for t in tables.values() for c in t.constraints if isinstance(c, CheckConstraint))
    print(f"REST endpoints (OpenAPI operations): {len(endpoints)}  ({len([e for e in endpoints if '/health' not in e])} excluding /health)")
    print(f"Database tables (excl. alembic_version): {len(tables)}")
    print(f"Explicit indexes: {explicit_indexes}   CHECK constraints: {checks}")


if __name__ == "__main__":
    main()
