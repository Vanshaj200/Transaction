"""FastAPI application: wiring only. Business logic lives in app/services."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import DomainError
from app.routers import auth, items, parties

app = FastAPI(
    title="BillBook",
    version="1.0.0",
    description="GST billing, inventory and double-entry accounting backend. Amounts are integer PAISE.",
)

app.include_router(auth.router)
app.include_router(parties.router)
app.include_router(items.router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
