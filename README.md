# BillBook

A GST billing, inventory and double-entry accounting **backend** (FastAPI + SQLAlchemy 2.0 + PostgreSQL).
There is no frontend: Swagger UI at `/docs` is the UI.

It is a portfolio project. It deliberately does **not** integrate with the GST portal: no GSTN lookups,
no e-invoice (IRN) and no e-way bill. GSTINs are validated locally (format + check digit) and that is all.

* Money is **integer paise** everywhere, GST rates are **integer basis points**. No floats, no Decimals.
* Creating an invoice saves it, moves stock and posts the journal in **one database transaction**.
* Overselling is prevented with `SELECT ... FOR UPDATE` row locks (proved by a 100-thread test, see below).
* Every journal entry balances: checked in code, by a deferred PostgreSQL trigger, and by tests.
* Invoices are never deleted. Cancelling posts a reversing entry.

## Contents
1. [Quick start](#quick-start) 2. [ER diagram](#er-diagram) 3. [Endpoints](#endpoints) 4. [API examples](#api-examples)
5. [Design decisions](#design-decisions) 6. [Tests](#tests) 7. [Measured results](#measured-results)
8. [Project layout](#project-layout) 9. [Known limitations](#known-limitations)

## Quick start

### With Docker
```bash
cp .env.example .env
docker compose up --build            # db + api (migrations run automatically) on http://localhost:8000/docs
docker compose exec api python -m scripts.seed          # optional: sample data (200 invoices)
docker compose run --rm tests                            # full test-suite on PostgreSQL
```
> The Dockerfile and docker-compose.yml were written and syntax-checked, but could **not** be run in the
> environment this project was built in (no Docker daemon). Everything else in this README was run.

### Without Docker
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # point DATABASE_URL at a PostgreSQL database you created
alembic upgrade head                 # creates the 10 tables, the chart of accounts and the DB triggers
python -m scripts.seed               # sample parties/items/invoices/payments + demo users
uvicorn app.main:app --reload        # http://127.0.0.1:8000/docs
```
Demo logins created by the seed script (development only): `admin@billbook.dev / admin12345`,
`accountant@billbook.dev / accountant12345`, `viewer@billbook.dev / viewer12345`.
On an empty database the **first** `POST /auth/register` creates an admin.
In Swagger: call `/auth/login`, then press **Authorize** and enter the email and password.

### Roles
| Role | Can do |
|---|---|
| `viewer` | every `GET` (masters, invoices, payments, reports, PDF) |
| `accountant` | viewer + create/update parties and items, create/cancel invoices, record payments |
| `admin` | accountant + deactivate (soft-delete) parties/items + register users |

Python 3.11 is the target (Dockerfile). It also runs on 3.13, which is what the tests were run with here.

## ER diagram
```mermaid
erDiagram
    USERS {
        int id PK
        string email UK
        string role "admin | accountant | viewer"
    }
    PARTIES {
        int id PK
        string party_type "customer | supplier"
        string gstin UK "nullable"
        string state_code "2-digit GST state"
    }
    ITEMS {
        int id PK
        string hsn
        int gst_rate_bp "1800 = 18%"
        bigint selling_price "paise"
        int stock_qty "CHECK >= 0"
    }
    INVOICES {
        int id PK
        string invoice_no UK
        string invoice_type "sale | purchase"
        string status "posted | cancelled"
        string supply_type "intra | inter"
        bigint grand_total "paise"
    }
    INVOICE_LINES {
        int id PK
        int quantity
        bigint unit_price
        int gst_rate_bp "snapshot"
        bigint cgst
        bigint sgst
        bigint igst
    }
    STOCK_MOVEMENTS {
        int id PK
        int qty_change "negative = out"
        string reason "opening|sale|purchase|sale_cancel|purchase_cancel"
    }
    ACCOUNTS {
        int id PK
        string code UK
        string account_type "asset|liability|income|expense"
    }
    JOURNAL_ENTRIES {
        int id PK
        date entry_date
        int reverses_entry_id FK,UK "set on a reversal"
    }
    JOURNAL_LINES {
        int id PK
        bigint debit
        bigint credit
    }
    PAYMENTS {
        int id PK
        bigint amount "paise"
        string mode "cash | bank"
    }

    PARTIES ||--o{ INVOICES : "billed to / from"
    INVOICES ||--|{ INVOICE_LINES : contains
    ITEMS ||--o{ INVOICE_LINES : "sold / bought as"
    ITEMS ||--o{ STOCK_MOVEMENTS : "history of"
    INVOICES ||--o{ STOCK_MOVEMENTS : causes
    INVOICES ||--o{ PAYMENTS : "settled by"
    INVOICES ||--o{ JOURNAL_ENTRIES : "posts (original + reversal)"
    PAYMENTS ||--o| JOURNAL_ENTRIES : posts
    JOURNAL_ENTRIES ||--|{ JOURNAL_LINES : has
    ACCOUNTS ||--o{ JOURNAL_LINES : "debited / credited"
    JOURNAL_ENTRIES ||--o| JOURNAL_ENTRIES : reverses
    USERS ||--o{ INVOICES : created_by
    USERS ||--o{ PAYMENTS : created_by
```

Chart of accounts (fixed, inserted by migration `0002`): 1000 Cash, 1010 Bank, 1100 Accounts Receivable,
1200/1210/1220 Input CGST/SGST/IGST, 2000 Accounts Payable, 2100/2110/2120 CGST/SGST/IGST Payable,
4000 Sales, 5000 Purchases.

## Endpoints
| Area | Endpoint | Role |
|---|---|---|
| Auth | `POST /auth/register` (first user = admin, then admin only), `POST /auth/login`, `GET /auth/me` | - |
| Parties | `POST/GET /parties`, `GET/PATCH/DELETE /parties/{id}` (DELETE = deactivate) | write: accountant, delete: admin |
| Items | `POST/GET /items`, `GET/PATCH/DELETE /items/{id}` | same |
| Invoices | `POST /invoices`, `GET /invoices`, `GET /invoices/{id}`, `POST /invoices/{id}/cancel`, `GET /invoices/{id}/pdf` | write: accountant |
| Payments | `POST /payments`, `GET /payments`, `GET /payments/{id}` | write: accountant |
| Reports | `GET /reports/trial-balance`, `/party-outstanding`, `/stock-summary`, `/gst-summary?date_from=&date_to=` | any role |
| Meta | `GET /health` | - |

`python -m scripts.stats` counts endpoints, tables, indexes and CHECK constraints from the code.

## API examples
```bash
B=http://localhost:8000
# 1. login (form fields) -> token
TOKEN=$(curl -s -X POST $B/auth/login -d 'username=accountant@billbook.dev&password=accountant12345' | jq -r .access_token)
H="Authorization: Bearer $TOKEN"

# 2. a customer in Karnataka (state 29) and an item; amounts are PAISE: 10000 = Rs 100.00
curl -s -X POST $B/parties -H "$H" -H 'Content-Type: application/json' -d '{
  "name":"Karnataka Tools Pvt Ltd","party_type":"customer","gstin":"29ABCDE1234F1Z5","state_code":"29"}'
curl -s -X POST $B/items -H "$H" -H 'Content-Type: application/json' -d '{
  "name":"Steel Bolt M8","hsn":"7318","gst_rate_bp":1800,"selling_price":10000,"opening_stock":100}'

# 3. sale invoice (company is in state 27, customer in 29 -> IGST)
curl -s -X POST $B/invoices -H "$H" -H 'Content-Type: application/json' -d '{
  "invoice_type":"sale","party_id":1,"lines":[{"item_id":1,"quantity":3}]}'
# -> taxable_total 30000, igst_total 5400, grand_total 35400, supply_type "inter", status "posted"
#    (and: stock 100 -> 97, journal: Dr Receivables 35400 / Cr Sales 30000 / Cr IGST Payable 5400)

# 4. part payment, then the reports
curl -s -X POST $B/payments -H "$H" -H 'Content-Type: application/json' -d '{"invoice_id":1,"amount":10000,"mode":"bank","reference":"UPI-8841"}'
curl -s $B/reports/trial-balance -H "$H" | jq '{total_debit,total_credit,net,balanced}'
curl -s $B/reports/party-outstanding -H "$H"
curl -s "$B/reports/gst-summary?date_from=2026-04-01&date_to=2026-04-30" -H "$H"
curl -s $B/reports/stock-summary -H "$H"

# 5. PDF, and cancel (reverses; a cancelled invoice with payments is refused)
curl -s -o INV-0000001.pdf $B/invoices/1/pdf -H "$H"
curl -s -X POST $B/invoices/1/cancel -H "$H" -H 'Content-Type: application/json' -d '{"reason":"wrong customer"}'
```
Errors are JSON `{"detail": "..."}`: `401` not logged in, `403` wrong role, `404` not found,
`409` business conflict (insufficient stock, overpayment, already cancelled), `422` invalid input.

## Design decisions

### 1. One transaction per business action
`services/invoicing.py::create_invoice` does, in this order, inside **one** SQLAlchemy session/transaction:

1. validate party (right type, active) and decide intra/inter-state;
2. lock all item rows (`SELECT ... FOR UPDATE`, ascending id);
3. compute each line's GST;
4. insert invoice + lines (the invoice number is derived from the generated id);
5. move stock (`stock_movements` row + `items.stock_qty`), refusing a sale that would go below zero;
6. post the journal entry (`services/ledger.py`);
7. `COMMIT`. Any exception anywhere -> `ROLLBACK`, so there is never an invoice without stock movement or
   journal, or vice versa. `tests/test_ledger.py` forces a failure after steps 4-5 and checks that no invoice,
   stock movement, journal row or stock change survived. Cancel and payment follow the same pattern.

Services never talk HTTP and routers never contain business logic, so the transaction boundary is in one place.

### 2. Locking and concurrency
* **Row locks**: step 2 uses `SELECT ... FOR UPDATE`. Two sales of the same item queue behind each other and the
  second one sees the *post-commit* stock. This is the oversell protection. PostgreSQL's default isolation
  (READ COMMITTED) is enough because the lock serialises the read-check-write on the item row.
* **`populate_existing`**: after the lock is granted the query refreshes any copy of the row already in the
  SQLAlchemy identity map, so the stock check never uses a stale value.
* **Lock ordering**: items are always locked in ascending id order, so two invoices that list the same items in
  opposite order cannot deadlock (tested).
* **Invoice-level locks**: cancel and payment lock the *invoice* row, so two simultaneous cancels reverse exactly
  once, and a payment cannot sneak in while an invoice is being cancelled, and concurrent payments cannot overpay.
* **Defence in depth**: `CHECK (stock_qty >= 0)` on `items`, a unique index allowing only one *original* journal
  entry per invoice, and `UNIQUE (reverses_entry_id)` so an entry can be reversed only once.
* **Proof that the lock is what matters**: with `.with_for_update()` removed from `inventory.lock_items`,
  the same 100-request test accepted 100 sales against 30 units of stock (the `CHECK` did not fire: both
  transactions read 30 and wrote 29). See [Tests](#tests).

### 3. Money handling
* All amounts are `BIGINT` **paise** (Rs 1 = 100). Integer arithmetic is exact, so ledger equality
  `debits == credits` can be tested with `==`, not with a tolerance.
* GST rates are `INTEGER` **basis points** (18% = 1800) and only the statutory slabs 0, 0.25, 3, 5, 12, 18, 28 % are
  accepted. Quantities are whole units.
* **Rounding**: `taxable = qty x unit_price` (exact). Tax is computed **per line** and rounded **half-up** with
  integer division (`round_div`), never with `float` or Python's banker's `round()`. Invoice totals are the *sum of the
  rounded lines*, never a rounding of the grand total. Intra-state: CGST and SGST are each `taxable x rate/2`, rounded
  separately, so they are always equal; inter-state: IGST is `taxable x rate`, rounded once. Because each component is
  rounded independently, CGST+SGST can differ from IGST on the same value by 1 paisa (test `test_gst.py`).
* Database `CHECK`s repeat the arithmetic (`taxable = qty x price`, `line_total = taxable + cgst + sgst + igst`,
  `grand_total = ...`, intra-state invoices cannot carry IGST), so a bug in app code cannot persist wrong totals.
* Money is converted to rupees only for display (`services/money.py`, Indian digit grouping) and in the PDF.
* PostgreSQL returns `SUM(bigint)` as `NUMERIC` (a `Decimal`); the code converts aggregates with `int()` explicitly
  (this was a real bug caught by running the tests on PostgreSQL, not SQLite).

### 4. Double-entry ledger
* Sale: `Dr Receivables (grand total) / Cr Sales (taxable) / Cr CGST+SGST or IGST Payable`.
  Purchase: `Dr Purchases (taxable) / Dr Input CGST+SGST or IGST / Cr Payables (grand total)`.
  Sale payment: `Dr Cash or Bank / Cr Receivables`. Purchase payment: `Dr Payables / Cr Cash or Bank`.
* **Balance rule enforced three times**: `ledger.validate_lines()` (before anything is written), a
  PostgreSQL *deferred constraint trigger* (checked at `COMMIT`, after all lines of the entry exist), and tests.
* **Cancellation = reversal**: the invoice row is kept; `status` becomes `cancelled`; stock is restored; and a *new*
  journal entry with every debit/credit swapped is posted, linked via `reverses_entry_id`. The original is never
  edited. Triggers make journal and stock-movement rows append-only and block `DELETE` on invoices/lines (PostgreSQL).
  Cancelling a purchase is refused if the stock has already been sold; cancelling an invoice that has payments is refused.
* **Sub-ledger = control account**: `scripts/check_integrity.py` verifies that the Receivables/Payables accounts equal
  the sum of party outstanding computed from invoices and payments, and that `items.stock_qty = SUM(stock_movements)`.
* No COGS / perpetual inventory valuation: the spec's chart of accounts has *Purchases* and *Sales* only
  (periodic-inventory style), so profit is not computed.

### 5. Indexes (each has a comment in the model explaining why)
| Index | Serves |
|---|---|
| `invoice_lines (invoice_id)` | lines of ONE invoice (detail, PDF, cancel); PostgreSQL does not index foreign keys automatically |
| `payments (invoice_id)` | paid amount of ONE invoice (shown on every invoice read, checked on every payment) |
| `invoices (invoice_type, status, invoice_date)` | `GET /invoices` by type and period, newest first: equality on type/status, then a date-ordered range scan |
| `invoices (party_id, status)` | "invoices of this party" |
| `journal_lines (entry_id)`, `journal_entries (invoice_id)` | lines of an entry (balance trigger, reversal), entries of an invoice |
| `stock_movements (item_id)`, `(invoice_id)`; `invoice_lines (item_id)` | FK lookups: stock card of an item, movements of an invoice |
| `parties (party_type, name)`, `items (name)` | list screens and name search |
| unique: `users.email`, `parties.gstin`, `invoices.invoice_no`, `accounts.code`, ... | lookups **and** integrity |
| partial unique `journal_entries (invoice_id) WHERE reverses_entry_id IS NULL` | exactly one original entry per invoice |

**Indexes were removed after measuring them.** The first version had wide "covering" indexes meant to speed up the
trial balance and GST summary. `scripts/bench_indexes.py` (below) showed no gain on those aggregate reports even at
100,000 invoices: they must read most of the table anyway, so PostgreSQL picks a sequential scan, and even with
`random_page_cost` lowered so that it did use them they were not faster. Those indexes were dropped. What the indexes *do* speed up are point lookups
and filtered lists (see the table in "Measured results"). The honest summary: indexes fix lookups; the aggregate
reports at this scale are limited by reading the rows, and the next step would be a summary table, which is
out of scope.

### 6. Other decisions
* **Auth**: bcrypt hashes; JWT (HS256) contains only the user id, and the role is read from the DB on every
  request, so changing a role or deactivating a user takes effect immediately. Registration is open only for the very
  first user (bootstrap admin); afterwards only an admin can create users, so nobody can self-register as accountant.
* **Soft delete** for parties and items (`is_active`); they are referenced by invoices and can never be removed.
* **Snapshots**: invoice lines copy item name, HSN, rate and unit price, so editing an item later does not change
  history. The invoice also stores `place_of_supply` (the party's state at that time).
* **Invoice numbers** (`INV-0000123`, `PUR-0000456`) come from the generated primary key. They are unique but **not
  gapless** (a rolled-back transaction burns an id). GST rules want consecutive series; a production system would use a
  locked counter row per series per financial year. Documented here instead of hidden.
* **Migrations**: `0001` is the autogenerated schema (reviewed by hand), `0002` inserts the chart of accounts and the
  PostgreSQL triggers. The tests build the PostgreSQL schema by running the migrations, and a test runs `alembic check`
  to prove models and migrations have not drifted. On SQLite (fallback for tests only) the schema comes from
  `create_all` and the triggers do not exist, so those tests are skipped there.
* **Dialect support**: everything is portable SQLAlchemy except row locks (`FOR UPDATE` is a no-op on SQLite) and the
  triggers. Concurrency claims are therefore only made for PostgreSQL.

## Tests
```bash
pytest                                               # SQLite: 102 tests, ~17 s (PostgreSQL-only tests are skipped)
TEST_DATABASE_URL=postgresql+psycopg://billbook:billbook@localhost:5432/billbook_test \
  pytest --cov=app --cov-report=term-missing         # full suite incl. migrations, triggers, concurrency
CONCURRENCY=200 TEST_DATABASE_URL=... pytest tests/test_concurrency.py   # more simultaneous requests
```
(The PostgreSQL server needs `max_connections` above the concurrency level; docker-compose sets 400.)

| File | What it proves |
|---|---|
| `test_gst.py` | intra-state split, inter-state IGST, half-up rounding, per-line (not per-total) rounding |
| `test_invoicing.py` | totals, stock movement, oversell rejected (also across duplicate lines), nothing saved on failure, snapshots |
| `test_ledger.py` | unbalanced/one-sided/negative entries rejected, posting shapes, ledger balances over 60 random invoices + cancels, **rollback on failure**, **cancel reversal**, trial balance nets to zero |
| `test_payments.py` | payment journal, no overpayment, no cancel-after-payment, receivables control = party outstanding |
| `test_reports.py` | trial balance (incl. `as_of`), stock summary + drift detection, GST summary by rate/period |
| `test_concurrency.py` (PG) | N simultaneous sales: exactly `stock` accepted, 0 oversell; opposite lock order: no deadlock; concurrent payments can't overpay; concurrent cancels reverse once; same oversell test over real HTTP (uvicorn) |
| `test_db_guards.py` (PG) | database refuses an unbalanced entry at COMMIT, updates/deletes of journal rows, deletes of invoices, double reversal; `alembic check` |
| `test_schema.py`, `test_auth.py`, `test_masters.py`, `test_gstin.py`, `test_pdf_money.py`, `test_misc.py` | constraints, roles, validation, PDF, formatting |

To see the lock working, remove `.with_for_update()` in `app/services/inventory.py` and re-run
`tests/test_concurrency.py`: the oversell tests fail.

## Measured results
Everything below was produced by commands in this repository, on one machine (a 4-core Linux container
running PostgreSQL 16, the API, the load generator and the tests together). Re-run them to get your own numbers;
absolute milliseconds will differ, the shape should not.

| What | Result | How to reproduce |
|---|---|---|
| REST endpoints | **26** (25 business endpoints + `/health`) | `python -m scripts.stats` |
| Database tables | **10** (+ `alembic_version`), 12 explicit indexes, 23 CHECK constraints, 2 migrations | `python -m scripts.stats` |
| Tests | **114 pass** on PostgreSQL (102 pass + 12 PostgreSQL-only skipped on SQLite) | `pytest`, `TEST_DATABASE_URL=... pytest` |
| Coverage | **99%** of `app/` (1,162 statements, 17 missed), measured on the PostgreSQL run | `TEST_DATABASE_URL=... pytest --cov=app` |
| Oversell protection | **0 oversells under 100 concurrent sale requests**: 100 threads (each its own DB connection, released together by a barrier) against an item with 30 units: exactly 30 accepted, 70 rejected with `InsufficientStock`, final stock 0. Same result through real HTTP (uvicorn), and with `CONCURRENCY=200`. Stable over 3 repeated runs. | `TEST_DATABASE_URL=... pytest tests/test_concurrency.py` |
| Same test without the lock | **100 of 100 sales accepted against 30 units** when `.with_for_update()` is removed from `inventory.lock_items` (so the test really detects the bug) | edit, re-run the test above |
| Ledger integrity | **10,000 seeded invoices** (7,509 sales, 2,491 purchases, 215 cancelled, 6,822 payments; 17,037 journal entries, 47,308 journal lines): total debits = total credits = 763,486,957,103 paise, **0 imbalanced entries, trial balance net 0**, receivables/payables control accounts equal the sum of party balances, stock = sum of movements. All 11 checks pass. | `python -m scripts.seed --reset --invoices 10000 && python -m scripts.check_integrity` (seeding takes ~2 min; deterministic) |
| Integrity after load | After the load test below the same 11 checks still pass on 17,010 invoices | `python -m scripts.check_integrity` |
| Invoice creation latency | one uvicorn worker, `locust`, sale invoices of 1-3 lines against the 10,000-invoice database: **1 user: p50 20 ms, p95 25 ms** (869 requests, 0 failures). 5 users: 64.6 req/s, p95 74 ms. 20 users: 58.7 req/s, p95 540 ms (saturated: one Python process on one core; throughput stops growing at about 60-65 req/s). 0 failures in all runs. | see "Load test" below |
| Index effect, point lookups | at **100,000 invoices / 250,251 lines / 68,556 payments** (PostgreSQL `EXPLAIN ANALYZE`, median of 9, default settings, two full runs): lines of one invoice **~12 ms -> ~0.01 ms**; paid amount of one invoice **3-4 ms -> ~0.01-0.02 ms**; newest 100 invoices of a party **9-11 ms -> ~0.09 ms**; newest 100 sales in a month **12-15 ms -> ~0.11 ms**. Measured as the API sees it (wall-clock incl. fetch): about 13 -> 0.6 ms, 4-6 -> 0.4-0.5 ms, 7-11 -> ~1 ms, 16 -> ~1.2 ms. Plan changes from `Seq Scan` to `Index Scan`. | `python -m scripts.bench_indexes` |
| Index effect, aggregate reports | **none**: GST summary (1 month: ~31 ms, full year: ~70 ms), party outstanding (~85-130 ms) and trial balance (~175-220 ms) are the same with and without the indexes (ratios 0.7x-1.3x, i.e. noise) because PostgreSQL scans the table either way. Not a claim of improvement. | same script |

### Load test
```bash
python -m scripts.seed --reset --invoices 10000
uvicorn app.main:app --port 8000 &
locust -f scripts/locustfile.py --headless -u 20 -r 10 -t 60s --host http://127.0.0.1:8000 --csv bench_out/locust
```
The first simulated user logs in as the seeded accountant and restocks every item with a purchase so the run measures
latency and not "insufficient stock" rejections (these restock purchases appear as 4 `POST /invoices` rows).

### Things you must NOT claim from this project
* Not "used by businesses", no user or revenue figures: there are none.
* Not "handles lakhs of invoices": the largest data set tested is 100,000 invoices (index benchmark) and
  17,010 invoices for the API/ledger checks.
* Not "sped up GST reports with indexes": measured, and it did not.
* The `docker compose` files were not executed here (no Docker daemon).

## Project layout
```
app/
  main.py            app wiring, error handler, thread-pool size
  config.py          settings (env / .env)      database.py   engine + session dependency
  security.py        bcrypt + JWT               deps.py       current user + role checks
  errors.py          domain errors -> HTTP status
  models/            one file per table (constraints + commented indexes live here)
  schemas/           Pydantic request/response models and validation
  routers/           thin HTTP layer: auth, parties, items, invoices, payments, reports
  services/
    gst.py           pure GST maths             gstin.py      GSTIN/state validation
    invoicing.py     create / cancel / list     inventory.py  row locks + stock changes
    ledger.py        double-entry posting       payments.py   record payments
    reports.py       the four reports           pdf.py        invoice PDF
    masters.py       party/item CRUD            accounts.py   chart-of-accounts constants
alembic/versions/    0001 schema, 0002 accounts + triggers
scripts/             seed.py, check_integrity.py, bench_indexes.py, locustfile.py, stats.py
tests/               see the table above
```

## Known limitations
* Whole-unit quantities; no discounts, no tax-inclusive prices, no round-off line, single company, single currency.
* No GSTN / e-invoice / e-way bill integration (by design), no GSTR filing formats.
* Invoice numbers are unique but not gapless (see above). No financial-year close / period locks.
* No payment reversal or refund; an invoice with payments cannot be cancelled.
* No COGS / stock valuation, so no profit & loss or balance sheet (only the trial balance).
* JWT has no refresh tokens or revocation list; no rate limiting; `SECRET_KEY` must be changed outside development.
* The SQLite fallback has no row locks and no database triggers; use PostgreSQL for anything that matters.
* Docker files were not executed in the build environment (no Docker daemon available).
