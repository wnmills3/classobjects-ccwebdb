"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import (
    auth,
    catalog,
    customers,
    images,
    inventory,
    orders,
    reference,
    users,
)

app = FastAPI(
    title="ccwebdb",
    description="Numismatic and currency inventory and sales platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(catalog.router, prefix=settings.api_prefix)
app.include_router(images.router, prefix=settings.api_prefix)
app.include_router(inventory.router, prefix=settings.api_prefix)
app.include_router(reference.router, prefix=settings.api_prefix)
app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(customers.router, prefix=settings.api_prefix)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe.

    Deliberately touches nothing, so it stays honest about the process rather
    than about the database.
    """
    return {"status": "ok"}
