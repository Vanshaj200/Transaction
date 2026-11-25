"""Load test for invoice creation (p95 latency).

Needs a running API on a seeded database (python -m scripts.seed --invoices 10000):

    uvicorn app.main:app --port 8000 &
    locust -f scripts/locustfile.py --headless -u 20 -r 5 -t 60s --host http://127.0.0.1:8000 --csv bench_out/locust

Each simulated user logs in as the seeded accountant and raises SALE invoices of 1-3 lines against
random customers and items. Items are topped up with a big purchase first (see on_start of the
first user) so the test measures latency, not "insufficient stock" rejections.
"""
import random
import threading

from locust import HttpUser, between, events, task

_lock = threading.Lock()
_ctx: dict = {}


def _bootstrap(client) -> None:
    """Once per run: load customers/items and restock every item so sales never run dry."""
    with _lock:
        if _ctx:
            return
        r = client.post("/auth/login", data={"username": "accountant@billbook.dev", "password": "accountant12345"})
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        customers = client.get("/parties?party_type=customer&limit=500", headers=headers).json()
        suppliers = client.get("/parties?party_type=supplier&limit=500", headers=headers).json()
        items = client.get("/items?limit=500", headers=headers).json()
        for chunk in range(0, len(items), 10):
            lines = [{"item_id": i["id"], "quantity": 1_000_000, "unit_price": 100} for i in items[chunk:chunk + 10]]
            client.post("/invoices", headers=headers, json={"invoice_type": "purchase", "party_id": suppliers[0]["id"], "lines": lines}).raise_for_status()
        _ctx.update(headers=headers, customers=[c["id"] for c in customers], items=[i["id"] for i in items])


class Accountant(HttpUser):
    wait_time = between(0.0, 0.05)

    def on_start(self):
        _bootstrap(self.client)

    @task
    def create_sale(self):
        item_ids = random.sample(_ctx["items"], random.randint(1, 3))
        body = {
            "invoice_type": "sale", "party_id": random.choice(_ctx["customers"]),
            "lines": [{"item_id": i, "quantity": random.randint(1, 5)} for i in item_ids],
        }
        self.client.post("/invoices", json=body, headers=_ctx["headers"], name="POST /invoices (sale)")


@events.quitting.add_listener
def _fail_on_errors(environment, **_):
    if environment.stats.total.num_failures:
        environment.process_exit_code = 1
