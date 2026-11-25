"""Seed sample data through the REAL service layer (so every invoice is GST-computed, stock-moved and
journal-posted exactly as in production).

    python -m scripts.seed                    # small demo data set (200 invoices)
    python -m scripts.seed --invoices 10000   # benchmark-sized data set
    python -m scripts.seed --reset            # wipe business data first (keeps the chart of accounts)

Demo logins (development only!):  admin@billbook.dev / admin12345, accountant@billbook.dev /
accountant12345, viewer@billbook.dev / viewer12345.
"""
import argparse
import random
import time
from datetime import date, timedelta

from sqlalchemy import select, text

from app.config import get_settings
from app.database import SessionLocal, engine
from app.errors import DomainError
from app.models import Account, Base, Invoice, Item, Party, User
from app.schemas.invoice import InvoiceCreate
from app.schemas.item import ItemCreate
from app.schemas.party import PartyCreate
from app.schemas.payment import PaymentCreate
from app.security import hash_password
from app.services import invoicing, masters, payments
from app.services.gst import ALLOWED_RATES_BP
from app.services.gstin import make_gstin

USERS = [("admin", "admin12345"), ("accountant", "accountant12345"), ("viewer", "viewer12345")]
STATES = ["27", "27", "27", "29", "24", "07", "33", "36", "09", "19"]   # 27 (our state) is over-represented
WORDS = ["Sharma", "Patel", "Iyer", "Reddy", "Khan", "Gupta", "Mehta", "Nair", "Singh", "Das", "Joshi", "Verma"]
KINDS = ["Traders", "Enterprises", "Industries", "Agencies", "Distributors", "Stores", "Supplies"]
ITEM_NAMES = ["Steel Bolt", "Copper Wire", "LED Bulb", "PVC Pipe", "Ball Bearing", "Hydraulic Pump", "Safety Helmet",
              "Welding Rod", "Paint Bucket", "Cement Bag", "Office Chair", "Printer Paper", "Network Cable", "Battery Pack",
              "Gear Box", "Drill Bit", "Spanner Set", "Water Tank", "Electric Motor", "Circuit Breaker"]
HSN = ["7318", "8544", "8539", "3917", "8482", "8413", "6506", "8311", "3208", "2523", "9401", "4802", "8544", "8507"]


def reset(db) -> None:
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name != "accounts"]
    db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    db.commit()


def seed_users(db) -> None:
    for role, password in USERS:
        email = f"{role}@billbook.dev"
        if not db.scalar(select(User).where(User.email == email)):
            db.add(User(email=email, hashed_password=hash_password(password), role=role, full_name=role.title()))
    db.commit()


def seed_masters(db, rng: random.Random, n_customers: int, n_suppliers: int, n_items: int):
    customers, suppliers, items = [], [], []
    for i in range(n_customers + n_suppliers):
        kind = "customer" if i < n_customers else "supplier"
        state = rng.choice(STATES)
        pan = f"{''.join(rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(5))}{1000 + i:04d}{rng.choice('ABCDEFGH')}"
        p = masters.create_party(db, PartyCreate(
            name=f"{rng.choice(WORDS)} {rng.choice(KINDS)} {i + 1}", party_type=kind, state_code=state,
            gstin=make_gstin(state, pan) if rng.random() < 0.9 else None, address=f"Plot {i + 1}, Industrial Area",
        ))
        (customers if kind == "customer" else suppliers).append(p)
    for i in range(n_items):
        items.append(masters.create_item(db, ItemCreate(
            name=f"{ITEM_NAMES[i % len(ITEM_NAMES)]} #{i + 1}", hsn=HSN[i % len(HSN)],
            gst_rate_bp=rng.choice([500, 1200, 1800, 1800, 2800]), unit="NOS",
            selling_price=rng.randint(50, 5000) * 100 + rng.choice([0, 50, 99]), opening_stock=0,
        )))
    return customers, suppliers, items


def run(args) -> None:
    rng = random.Random(args.seed)
    with SessionLocal() as db:
        if not db.scalar(select(Account.id).limit(1)):
            raise SystemExit("Chart of accounts is empty. Run `alembic upgrade head` first.")
        if args.reset:
            reset(db)
        elif db.scalar(select(Invoice.id).limit(1)):
            raise SystemExit("Database already has invoices. Use --reset to wipe business data first.")
        seed_users(db)
        customers, suppliers, items = seed_masters(db, rng, max(20, args.invoices // 50), 15, 40)
        stock = {it.id: 0 for it in items}
        price = {it.id: it.selling_price for it in items}

        start, end = date(2025, 4, 1), date(2026, 3, 31)
        span = (end - start).days
        counts = {"sale": 0, "purchase": 0, "cancelled": 0, "payments": 0, "rejected": 0}
        t0 = time.time()
        for n in range(args.invoices):
            day = start + timedelta(days=int(span * n / args.invoices))
            in_stock = [i for i in items if stock[i.id] > 0]
            kind = "purchase" if (rng.random() < 0.25 or len(in_stock) < 3) else "sale"
            try:
                if kind == "purchase":
                    chosen = rng.sample(items, rng.randint(1, 4))
                    lines = [{"item_id": i.id, "quantity": rng.randint(100, 600),
                              "unit_price": int(price[i.id] * rng.uniform(0.55, 0.8))} for i in chosen]
                    party = rng.choice(suppliers)
                else:
                    chosen = rng.sample(in_stock, min(len(in_stock), rng.randint(1, 4)))
                    lines = [{"item_id": i.id, "quantity": rng.randint(1, max(1, min(20, stock[i.id])))} for i in chosen]
                    party = rng.choice(customers)
                inv = invoicing.create_invoice(db, InvoiceCreate(
                    invoice_type=kind, party_id=party.id, invoice_date=day, lines=lines))
            except DomainError:
                counts["rejected"] += 1
                continue
            counts[kind] += 1
            sign = 1 if kind == "purchase" else -1
            for ln in lines:
                stock[ln["item_id"]] += sign * ln["quantity"]

            if rng.random() < 0.02:                                 # ~2% get cancelled
                invoicing.cancel_invoice(db, inv.id, "seed: entered by mistake")
                for ln in lines:
                    stock[ln["item_id"]] -= sign * ln["quantity"]
                counts["cancelled"] += 1
                continue
            if rng.random() < 0.7:                                  # ~70% get a payment (half full, half partial)
                amount = inv.grand_total if rng.random() < 0.5 else max(1, inv.grand_total * rng.randint(20, 80) // 100)
                payments.record_payment(db, PaymentCreate(
                    invoice_id=inv.id, amount=amount, mode=rng.choice(["cash", "bank", "bank"]),
                    payment_date=min(day + timedelta(days=rng.randint(0, 30)), date.today()),
                ))
                counts["payments"] += 1
            if (n + 1) % 500 == 0:
                print(f"  {n + 1}/{args.invoices} invoices ({time.time() - t0:.0f}s)")
        print(f"Seeded in {time.time() - t0:.1f}s: {counts}")
        print(f"company state {get_settings().company_state_code}; logins: admin@billbook.dev / admin12345 (dev only)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--invoices", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same data)")
    parser.add_argument("--reset", action="store_true", help="wipe business data first")
    run(parser.parse_args())
