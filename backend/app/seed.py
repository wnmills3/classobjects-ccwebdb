"""Create the first administrator and a small demo catalogue.

Reference data is *not* seeded here -- that is `app.seeding`, which loads the
shipped vocabulary from `backend/data/reference/`. Run that first; this module
assumes the classifiers it names already exist.

    python -m app.seeding load
    python -m app.seed
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import (
    Authenticity,
    Country,
    Currency,
    Denomination,
    Disposition,
    Grade,
    GradingService,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    Metal,
    ProvenanceSource,
    ReferenceMixin,
    StorageForm,
    User,
    UserRole,
    ValuationBasis,
)
from .security import hash_password

#: A demo catalogue. Classifiers are given as codes, matching how they cross
#: the API -- see `app.references`.
SAMPLE_CATALOG: list[dict] = [
    {
        "title": "1881-S Morgan Silver Dollar",
        "description": "Brilliant uncirculated, exceptional strike and lustre.",
        "item_kind": "coin",
        "country": "US",
        "denomination": "usd_coin_1_00",
        "grade": "MS64",
        "grading_service": "PCGS",
        "year_start": 1881,
        "price": Decimal("189.00"),
        "quantity_available": 5,
    },
    {
        "title": "1916-D Mercury Dime",
        "description": "Key date. Strong rims, fully readable mint mark.",
        "item_kind": "coin",
        "country": "US",
        "denomination": "usd_coin_0_10",
        "grade": "G4",
        "year_start": 1916,
        "price": Decimal("1250.00"),
        "quantity_available": 1,
    },
    {
        "title": "2021 American Silver Eagle",
        "description": "One troy ounce of .999 fine silver, Type 2 reverse.",
        "item_kind": "bullion",
        "country": "US",
        "metal": "silver",
        "grade": "MS70",
        "year_start": 2021,
        "price": Decimal("46.50"),
        "quantity_available": 20,
        "fineness": Decimal("0.9990"),
        "gross_weight_ozt": Decimal("1.000000"),
        "fine_weight_ozt": Decimal("0.999000"),
    },
    {
        "title": "1957-B $1 Silver Certificate",
        "description": "Blue seal. Crisp, well centred, bright paper.",
        "item_kind": "currency",
        "country": "US",
        "denomination": "usd_note_1",
        "grade": "UNC",
        "year_start": 1957,
        "price": Decimal("24.00"),
        "quantity_available": 8,
    },
    {
        "title": "1964 Kennedy Half Dollar",
        "description": "The only 90% silver year for the Kennedy half.",
        "item_kind": "coin",
        "country": "US",
        "denomination": "usd_coin_0_50",
        "grade": "AU58",
        "year_start": 1964,
        "price": Decimal("18.75"),
        "quantity_available": 12,
    },
]


def _code_id(db: Session, model: type[ReferenceMixin], code: str | None) -> int | None:
    if not code:
        return None
    found = db.execute(select(model.id).where(model.code == code)).scalar_one_or_none()
    if found is None:
        raise SystemExit(
            f"{model.__tablename__} has no code {code!r}. "
            "Run `python -m app.seeding load` first."
        )
    return found


def _build(db: Session, row: dict) -> None:
    item = InventoryItem(
        source_title=row["title"],
        description=row.get("description", ""),
        year_start=row.get("year_start"),
        fineness=row.get("fineness"),
        gross_weight_ozt=row.get("gross_weight_ozt"),
        fine_weight_ozt=row.get("fine_weight_ozt"),
        item_kind_id=_code_id(db, ItemKind, row["item_kind"]),
        country_id=_code_id(db, Country, row.get("country")),
        denomination_id=_code_id(db, Denomination, row.get("denomination")),
        grade_id=_code_id(db, Grade, row.get("grade")),
        grading_service_id=_code_id(db, GradingService, row.get("grading_service")),
        metal_id=_code_id(db, Metal, row.get("metal")),
        storage_form_id=_code_id(db, StorageForm, "single"),
        authenticity_id=_code_id(db, Authenticity, "genuine"),
        status_id=_code_id(db, ItemStatus, "received"),
        disposition_id=_code_id(db, Disposition, "listed"),
        valuation_basis_id=_code_id(db, ValuationBasis, "numismatic"),
        source=ProvenanceSource.seeded,
    )
    db.add(item)
    db.flush()

    db.add(
        Listing(
            inventory_item_id=item.id,
            price=row["price"],
            currency_id=_code_id(db, Currency, "USD"),
            quantity_available=row["quantity_available"],
            is_active=True,
        )
    )


def seed() -> None:
    """Create the first administrator and a small demo catalogue.

    Assumes the reference vocabularies are already loaded; run
    `python -m app.seeding load` first.
    """
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
        for row in SAMPLE_CATALOG:
            # Matched on source_title: the schema has no artificial unique key
            # per catalogue row, because two identical coins are two objects.
            exists = db.scalar(
                select(InventoryItem.id).where(
                    InventoryItem.source_title == row["title"]
                )
            )
            if exists is None:
                _build(db, row)
                created += 1

        print(
            f"created {created} catalogue item(s); "
            f"{len(SAMPLE_CATALOG) - created} already present"
        )
        db.commit()


if __name__ == "__main__":
    seed()
