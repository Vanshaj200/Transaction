"""FastAPI application: wiring only. Business logic lives in app/services."""
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.errors import DomainError
from app.routers import auth, invoices, items, parties, payments, reports


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Size of the thread pool that runs the (synchronous) endpoint functions.
    anyio.to_thread.current_default_thread_limiter().total_tokens = get_settings().worker_threads
    yield


app = FastAPI(
    lifespan=lifespan,
    title="BillBook",
    version="1.0.0",
    description="GST billing, inventory and double-entry accounting backend. Amounts are integer PAISE.",
)

app.include_router(auth.router)
app.include_router(parties.router)
app.include_router(items.router)
app.include_router(invoices.router)
app.include_router(payments.router)
app.include_router(reports.router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
