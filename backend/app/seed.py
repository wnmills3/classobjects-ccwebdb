"""Seed the database with an administrator and sample inventory.

Run with:  uv run python -m app.seed      (from the backend/ directory)

Safe to run repeatedly: existing rows are matched on their natural key and
left untouched.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import Coin, ItemKind, User, UserRole
from .security import hash_password

SAMPLE_INVENTORY = [
    {
        "sku": "US-MORGAN-1881S",
        "title": "1881-S Morgan Silver Dollar",
        "description": "Brilliant uncirculated, exceptional strike from the San Francisco mint.",
        "kind": ItemKind.coin,
        "country": "United States",
        "year": 1881,
        "denomination": "1 Dollar",
        "composition": "90% silver, 10% copper",
        "grade": "MS-64",
        "certification": "PCGS",
        "mint_mark": "S",
        "price": Decimal("189.00"),
        "quantity": 4,
    },
    {
        "sku": "US-SAINT-1924",
        "title": "1924 Saint-Gaudens Double Eagle",
        "description": "Classic 20 dollar gold piece, original mint lustre.",
        "kind": ItemKind.coin,
        "country": "United States",
        "year": 1924,
        "denomination": "20 Dollars",
        "composition": "90% gold",
        "grade": "MS-63",
        "certification": "NGC",
        "mint_mark": "",
        "price": Decimal("2650.00"),
        "quantity": 1,
    },
    {
        "sku": "GB-SOV-1900",
        "title": "1900 Victoria Old Head Gold Sovereign",
        "description": "London mint, well struck with light handling marks.",
        "kind": ItemKind.coin,
        "country": "United Kingdom",
        "year": 1900,
        "denomination": "1 Sovereign",
        "composition": "22 carat gold",
        "grade": "AU-58",
        "certification": "",
        "mint_mark": "",
        "price": Decimal("615.00"),
        "quantity": 3,
    },
    {
        "sku": "US-FRN-1934-1000",
        "title": "1934 $1000 Federal Reserve Note",
        "description": "Grover Cleveland high-denomination note, Chicago district.",
        "kind": ItemKind.banknote,
        "country": "United States",
        "year": 1934,
        "denomination": "1000 Dollars",
        "composition": "Paper",
        "grade": "VF-30",
        "certification": "PMG",
        "mint_mark": "",
        "price": Decimal("4200.00"),
        "quantity": 1,
    },
    {
        "sku": "CA-MAPLE-2021",
        "title": "2021 Canadian Silver Maple Leaf",
        "description": "One troy ounce of .9999 fine silver, sealed in original mint tube packaging.",
        "kind": ItemKind.coin,
        "country": "Canada",
        "year": 2021,
        "denomination": "5 Dollars",
        "composition": ".9999 fine silver",
        "grade": "BU",
        "certification": "",
        "mint_mark": "",
        "price": Decimal("38.50"),
        "quantity": 25,
    },
]


def seed() -> None:
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == settings.first_admin_email))
        if admin is None:
            admin = User(
                email=settings.first_admin_email,
                full_name="Site Administrator",
                hashed_password=hash_password(settings.first_admin_password),
                role=UserRole.admin,
            )
            db.add(admin)
            print(f"created admin {settings.first_admin_email}")
        else:
            print(f"admin {settings.first_admin_email} already exists")

        created = 0
        for row in SAMPLE_INVENTORY:
            if db.scalar(select(Coin).where(Coin.sku == row["sku"])) is None:
                db.add(Coin(**row))
                created += 1
        print(f"created {created} inventory item(s); {len(SAMPLE_INVENTORY) - created} already present")

        db.commit()


if __name__ == "__main__":
    seed()
