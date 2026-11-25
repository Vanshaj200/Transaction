"""Real concurrency against PostgreSQL: many threads, each with its OWN session/connection, all released
at the same instant by a barrier. These tests are why SELECT ... FOR UPDATE exists.

Set CONCURRENCY=200 (etc.) to change the number of simultaneous requests (default 100).
"""
import os
import random
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select

from app.config import get_settings
from app.errors import Conflict, InsufficientStock
from app.models import Invoice, Item, JournalEntry, Payment, StockMovement
from app.schemas.invoice import InvoiceCreate
from app.schemas.payment import PaymentCreate
from app.services import invoicing, payments
from tests.factories import make_item, make_party, sale
from tests.helpers import totals, unbalanced_entries

pytestmark = pytest.mark.postgres
N = int(os.environ.get("CONCURRENCY", "100"))


def run_concurrently(session_factory, n, fn):
    """Run fn(session, i) in n threads released together. Returns [(ok, result_or_exception)]."""
    barrier = threading.Barrier(n)

    def worker(i):
        with session_factory() as s:
            barrier.wait()                      # everyone starts at the same moment
            try:
                return True, fn(s, i)
            except Exception as exc:            # noqa: BLE001 - we classify below
                return False, exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))


def sale_payload(party, item, qty=1):
    return InvoiceCreate(invoice_type="sale", party_id=party.id, lines=[{"item_id": item.id, "quantity": qty}])


def assert_integrity(db, item_ids):
    db.expire_all()
    assert unbalanced_entries(db) == []
    d, c = totals(db)
    assert d == c
    for item_id in item_ids:
        stock = db.get(Item, item_id).stock_qty
        moved = db.scalar(select(func.sum(StockMovement.qty_change)).where(StockMovement.item_id == item_id))
        assert stock == moved and stock >= 0


def test_no_oversell_under_concurrent_sales(db, session_factory):
    stock = 30
    party, item = make_party(db), make_item(db, stock=stock)
    results = run_concurrently(session_factory, N, lambda s, i: invoicing.create_invoice(s, sale_payload(party, item)))
    ok = [r for good, r in results if good]
    rejected = [r for good, r in results if not good]
    assert all(isinstance(r, InsufficientStock) for r in rejected), [r for r in rejected if not isinstance(r, InsufficientStock)]
    assert len(ok) == stock and len(rejected) == N - stock          # exactly the stock that existed was sold
    assert len({inv.invoice_no for inv in ok}) == stock              # unique invoice numbers
    db.expire_all()
    assert db.get(Item, item.id).stock_qty == 0
    assert db.scalar(select(func.count(Invoice.id))) == stock
    assert db.scalar(select(func.count(JournalEntry.id))) == stock   # one journal entry per accepted invoice only
    assert_integrity(db, [item.id])


def test_no_oversell_with_mixed_quantities(db, session_factory):
    rng = random.Random(42)
    party, item = make_party(db), make_item(db, stock=100)
    qtys = [rng.randint(1, 5) for _ in range(N)]
    results = run_concurrently(session_factory, N, lambda s, i: invoicing.create_invoice(s, sale_payload(party, item, qtys[i])))
    sold = sum(qtys[i] for i, (good, _) in enumerate(results) if good)
    assert sold <= 100
    assert all(isinstance(r, InsufficientStock) for good, r in results if not good)
    db.expire_all()
    assert db.get(Item, item.id).stock_qty == 100 - sold
    assert_integrity(db, [item.id])


def test_opposite_lock_order_does_not_deadlock(db, session_factory):
    """Half the requests list item A then B, half list B then A. Locks are always taken in id order,
    so they queue instead of deadlocking (PostgreSQL would abort one with 'deadlock detected')."""
    party = make_party(db)
    a, b = make_item(db, stock=1000), make_item(db, stock=1000)

    def fn(s, i):
        lines = [{"item_id": a.id, "quantity": 1}, {"item_id": b.id, "quantity": 1}]
        if i % 2:
            lines.reverse()
        return invoicing.create_invoice(s, InvoiceCreate(invoice_type="sale", party_id=party.id, lines=lines))

    results = run_concurrently(session_factory, N, fn)
    errors = [r for good, r in results if not good]
    assert errors == []
    db.expire_all()
    assert db.get(Item, a.id).stock_qty == db.get(Item, b.id).stock_qty == 1000 - N
    assert_integrity(db, [a.id, b.id])


def test_concurrent_payments_cannot_overpay(db, session_factory):
    inv = sale(db, make_party(db), [(make_item(db, price=10_000), 1)])      # grand total 11,800
    results = run_concurrently(session_factory, N, lambda s, i: payments.record_payment(
        s, PaymentCreate(invoice_id=inv.id, amount=1_000, mode="bank")))
    assert all(isinstance(r, Conflict) for good, r in results if not good)
    assert sum(1 for good, _ in results if good) == 11                       # 11 x 1,000 fits; the 12th would exceed 11,800
    db.expire_all()
    assert db.scalar(select(func.sum(Payment.amount)).where(Payment.invoice_id == inv.id)) == 11_000
    assert_integrity(db, [])


def test_concurrent_cancels_reverse_exactly_once(db, session_factory):
    party, item = make_party(db), make_item(db, stock=10)
    inv = sale(db, party, [(item, 4)])
    results = run_concurrently(session_factory, 20, lambda s, i: invoicing.cancel_invoice(s, inv.id))
    assert sum(1 for good, _ in results if good) == 1
    assert all(isinstance(r, Conflict) for good, r in results if not good)
    db.expire_all()
    assert db.get(Item, item.id).stock_qty == 10                             # restored once, not 20 times
    assert db.scalar(select(func.count(JournalEntry.id)).where(JournalEntry.invoice_id == inv.id)) == 2
    assert_integrity(db, [item.id])


# ------------------------------------------------------------------ same thing through real HTTP
@pytest.fixture
def live_server(session_factory):
    """Run the real app under uvicorn in a background thread, using the test database."""
    from app.database import get_db
    from app.main import app

    settings = get_settings()
    old_threads = settings.worker_threads
    settings.worker_threads = max(old_threads, N + 20)      # let N requests be in flight at once

    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    app.dependency_overrides.clear()
    settings.worker_threads = old_threads


def test_http_no_oversell_under_concurrent_requests(db, headers, live_server):
    stock = 30
    party, item = make_party(db), make_item(db, stock=stock)
    auth = headers("accountant")
    body = {"invoice_type": "sale", "party_id": party.id, "lines": [{"item_id": item.id, "quantity": 1}]}
    barrier = threading.Barrier(N)

    def fire(_):
        with httpx.Client(base_url=live_server, headers=auth, timeout=60) as c:
            barrier.wait()
            return c.post("/invoices", json=body).status_code

    with ThreadPoolExecutor(max_workers=N) as pool:
        codes = list(pool.map(fire, range(N)))
    assert codes.count(201) == stock and codes.count(409) == N - stock and len(codes) == N
    db.expire_all()
    assert db.get(Item, item.id).stock_qty == 0
    assert_integrity(db, [item.id])
