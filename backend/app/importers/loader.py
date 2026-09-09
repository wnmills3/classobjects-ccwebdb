"""Normalised fields -> target schema rows.

DURABLE. This is the second half of the engine/profile seam: a profile knows
what the *source* columns mean and emits a normalised vocabulary; this module
knows what the *schema* means and writes rows. Neither knows the other's
subject matter, so replacing the profile does not touch this file.

Two rules govern everything here:

**A classifier is resolved, never invented as free text.** A value that is not
in the seeded vocabulary becomes a real reference row marked ``derived``, so it
is queryable like any other and visibly distinguishable from curated data. That
distinction is what makes `python -m app.seeding export --source seeded` safe
to share and `--source seeded derived` a deliberate choice.

**Nothing the source said is discarded.** Every parsed value keeps its raw text
alongside, because a parser can be wrong and re-parsing must stay possible.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Authenticity,
    BullionForm,
    CoinDetail,
    Composition,
    Country,
    Currency,
    CurrencyDetail,
    Denomination,
    Disposition,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemCertification,
    ItemKind,
    ItemNoteAttribute,
    ItemStatus,
    ItemStatusHistory,
    Metal,
    Mint,
    NoteAttribute,
    ProvenanceSource,
    PurchaseOrder,
    ReferenceMixin,
    SealColor,
    SetForm,
    StorageForm,
    ValuationBasis,
    Vendor,
    VendorKind,
)
from ..order_repair import identify

__all__ = ["SchemaLoader"]

#: Fallbacks used when a row says nothing about these. Every one is a real
#: seeded code, so a missing value never becomes NULL in a NOT NULL column.
DEFAULT_ITEM_KIND = "unknown"
DEFAULT_STORAGE_FORM = "single"
DEFAULT_AUTHENTICITY = "unverified"
DEFAULT_DISPOSITION = "held"
DEFAULT_STATUS = "received"
DEFAULT_VALUATION_BASIS = "numismatic"

#: Status markers a profile may emit, mapped to item_status codes.
STATUS_MARKERS: dict[str, str] = {
    "cancelled": "canceled",
    "canceled": "canceled",
    "returned": "returned",
    "missing": "missing",
    "ordered": "ordered",
    "received": "received",
}

_NON_CODE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """A stable reference code from a human label.

    ``"Silver Eagle"`` and ``"silver  eagle"`` must reach the same row, so the
    code is case-folded, stripped of punctuation and space-collapsed. Accents
    are folded too: a stray non-ASCII character in a source must not create a
    second row for a concept that already exists.
    """
    normalised = unicodedata.normalize("NFKD", text)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    return _NON_CODE.sub("_", ascii_only.strip().lower()).strip("_")


class SchemaLoader:
    """Writes one normalised row into the target schema.

    Reference lookups are cached for the life of the loader. A collection of
    several thousand rows resolves the same few dozen classifiers over and
    over, and without the cache each row would cost a query per foreign key.
    """

    def __init__(self, session: Session, *, create_missing: bool = True) -> None:
        """Caches every reference lookup for the life of the load."""
        self.session = session
        #: When False, an unknown classifier raises instead of creating a row.
        #: Useful for a strict re-run once the vocabulary has settled.
        self.create_missing = create_missing
        self._codes: dict[tuple[str, str], int] = {}
        self._vendors: dict[str, int] = {}
        self._orders: dict[tuple[int, str], int] = {}
        self._grades: dict[str, int] | None = None
        #: Counts of reference rows this loader invented, by table.
        self.derived: dict[str, int] = {}

    # -- reference resolution ---------------------------------------------

    def code_id(
        self,
        model: type[ReferenceMixin],
        code: str | None,
        *,
        label: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> int | None:
        """Resolve a reference code to an id, creating a derived row if needed."""
        if not code:
            return None
        table = model.__tablename__
        key = (table, code)
        if key in self._codes:
            return self._codes[key]

        found = self.session.execute(
            select(model.id).where(model.code == code)
        ).scalar_one_or_none()

        if found is None:
            if not self.create_missing:
                raise LookupError(f"{table}: no row with code {code!r}")
            row = model(
                code=code,
                label=label or code,
                source=ProvenanceSource.derived,
                **(extra or {}),
            )
            self.session.add(row)
            self.session.flush()
            found = row.id
            self.derived[table] = self.derived.get(table, 0) + 1

        self._codes[key] = found
        return found

    def by_label(
        self, model: type[ReferenceMixin], label: str | None, **extra: object
    ) -> int | None:
        """Resolve by human label, slugging it into a code first."""
        if not label:
            return None
        return self.code_id(model, slug(label), label=label, extra=extra or None)

    # -- acquisition -------------------------------------------------------

    def vendor_id(self, name: str | None, url: str | None = None) -> int | None:
        """Find or create the vendor with this name."""
        if not name:
            return None
        if name in self._vendors:
            return self._vendors[name]

        found = self.session.execute(
            select(Vendor.id).where(Vendor.name == name)
        ).scalar_one_or_none()
        if found is None:
            vendor = Vendor(
                name=name,
                url=url,
                host=_host_of(url),
                vendor_kind_id=self.code_id(VendorKind, "unknown"),
            )
            self.session.add(vendor)
            self.session.flush()
            found = vendor.id
        self._vendors[name] = found
        return found

    def purchase_order_id(
        self,
        vendor_id: int | None,
        order_number: str | None,
        ordered_on: date | None = None,
        source_url: str | None = None,
    ) -> int | None:
        """One order, many items -- so the same order number reuses its row.

        **A blank order number is not an order number.** Keying on
        ``order_number or ""`` collapsed every numberless row from one vendor
        into a single fabricated order: 1,922 eBay purchases across two years
        became `purchase_order` 114. That is treating *unknown* as a value.

        Where the number is missing, the vendor's own transaction id is often
        in the URL -- a HiBid or Proxibid lot, a LiveAuctioneers item, an Etsy
        receipt -- and that identifies the purchase properly. An eBay item
        number names a *listing* rather than a purchase, so it groups the rows
        but is never written into ``order_number``. With neither, the row gets
        no order at all, which is the honest answer.
        """
        if vendor_id is None:
            return None

        identified = identify(source_url) if not order_number else None
        if not order_number and identified is None:
            return None
        if identified is not None:
            identifier, is_order_number = identified
            order_number = identifier if is_order_number else None
            key = (vendor_id, f"url:{identifier}")
        else:
            key = (vendor_id, order_number or "")
        if key in self._orders:
            return self._orders[key]

        order = None
        if order_number:
            order = self.session.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.vendor_id == vendor_id,
                    PurchaseOrder.order_number == order_number,
                )
            ).scalar_one_or_none()

        if order is None:
            order = PurchaseOrder(
                vendor_id=vendor_id,
                order_number=order_number,
                ordered_on=ordered_on,
                source_url=source_url,
            )
            self.session.add(order)
            self.session.flush()

        self._orders[key] = order.id
        return order.id

    # -- the item ----------------------------------------------------------

    def load(
        self, kind: str, subtype: str | None, fields: dict[str, Any]
    ) -> InventoryItem:
        """Create one inventory item, its detail row and its opening history."""
        kind_code = kind if kind else DEFAULT_ITEM_KIND
        item_kind_id = self.code_id(ItemKind, kind_code, label=kind_code.title())

        bullion_form_id = None
        set_form_id = None
        if subtype and kind == "bullion":
            bullion_form_id = self.by_label(BullionForm, subtype)
        elif subtype and kind == "set":
            set_form_id = self.by_label(SetForm, subtype)

        storage_form_id = self.by_label(
            StorageForm, fields.get("storage_form") or DEFAULT_STORAGE_FORM
        )

        status_code = STATUS_MARKERS.get(
            str(fields.get("status_marker", "")).casefold(), DEFAULT_STATUS
        )

        grade_id = self._grade_id(fields.get("grade_raw"), fields)
        metal_id, fineness = self._metal_and_fineness(bullion_form_id, fields)

        # Face value -> denomination -> composition is the chain that makes
        # melt valuation work without recording metal content per item.
        denomination_id = self._denomination_id(fields)
        country_id = self._country_id(denomination_id)

        gross = _as_decimal(fields.get("weight_ozt"))
        fine = self._fine_weight(gross, fineness, fields)

        composition = self.resolve_composition(
            denomination_id, country_id, fields.get("year_start")
        )
        composition_id = composition.id if composition else None
        if composition is not None:
            # A public fact beats an absent one, but never overrides a weight
            # the source actually stated.
            if metal_id is None:
                metal_id = composition.metal_id
            if fineness is None:
                fineness = composition.fineness
            if gross is None:
                gross = composition.gross_weight_ozt
            if fine is None:
                fine = composition.fine_weight_ozt

        vendor_id = self.vendor_id(fields.get("vendor_name"), fields.get("vendor_url"))
        order_id = self.purchase_order_id(
            vendor_id,
            fields.get("order_number"),
            fields.get("ordered_on"),
            # The *item* link, not the seller's page. Only the item URL carries
            # the venue's transaction id that `identify()` looks for.
            fields.get("listing_url"),
        )

        numismatic = _as_decimal(fields.get("numismatic_value"))
        basis = "melt" if (fine and not numismatic) else DEFAULT_VALUATION_BASIS

        item = InventoryItem(
            purchase_order_id=order_id,
            item_kind_id=item_kind_id,
            denomination_id=denomination_id,
            country_id=country_id,
            composition_id=composition_id,
            bullion_form_id=bullion_form_id,
            set_form_id=set_form_id,
            storage_form_id=storage_form_id,
            piece_count=max(1, int(fields.get("storage_quantity") or 1)),
            year_start=fields.get("year_start"),
            year_end=fields.get("year_end"),
            grade_id=grade_id,
            grade_designation_id=self.code_id(
                GradeDesignation, _upper_or_none(fields.get("grade_designation"))
            ),
            grading_service_id=self.code_id(
                GradingService, _upper_or_none(fields.get("grading_service"))
            ),
            authenticity_id=self.code_id(Authenticity, DEFAULT_AUTHENTICITY),
            status_id=self.code_id(ItemStatus, status_code),
            disposition_id=self.code_id(Disposition, DEFAULT_DISPOSITION),
            local_catalog_number=fields.get("local_catalog_number"),
            source_title=_clip(fields.get("title") or "", 500),
            description=fields.get("description") or "",
            listing_url=fields.get("listing_url"),
            notes_raw=fields.get("comment"),
            denom_raw=fields.get("denom_raw"),
            year_raw=fields.get("year_raw"),
            grade_raw=fields.get("grade_raw"),
            item_cost=_as_decimal(fields.get("price")) or Decimal("0.00"),
            shipping_cost=_as_decimal(fields.get("shipping")) or Decimal("0.00"),
            numismatic_value=numismatic,
            valuation_basis_id=self.code_id(ValuationBasis, basis),
            metal_id=metal_id,
            fineness=fineness,
            gross_weight_ozt=gross,
            fine_weight_ozt=fine,
            weight_raw=fields.get("weight_raw"),
            attributes=_attributes(fields),
            source=ProvenanceSource.derived,
        )
        self.session.add(item)
        self.session.flush()

        self._add_detail(item, kind, fields)
        self._add_certification(item, fields)

        self.session.add(
            ItemStatusHistory(
                inventory_item_id=item.id,
                from_status_id=None,
                to_status_id=item.status_id,
                changed_at=datetime.now(UTC),
                note="set at import",
            )
        )
        return item

    # -- pieces ------------------------------------------------------------

    def _add_detail(self, item: InventoryItem, kind: str, fields: dict) -> None:
        if kind == "currency":
            self.session.add(
                CurrencyDetail(
                    inventory_item_id=item.id,
                    series_year=fields.get("year_start"),
                    series_letter=fields.get("series_letter"),
                    serial_number=fields.get("serial_number"),
                    seal_color_id=self.code_id(SealColor, fields.get("seal_color")),
                )
            )
            # Note features are a many-to-many link, not a column: a note can
            # be both a star note and a fancy serial.
            for code in fields.get("note_attributes") or []:
                attribute_id = self.code_id(NoteAttribute, code)
                if attribute_id is not None:
                    self.session.add(
                        ItemNoteAttribute(
                            inventory_item_id=item.id,
                            note_attribute_id=attribute_id,
                        )
                    )
            return

        # A mint set carrying both P and D marks is one item, so the first
        # mark is recorded and the full list is kept in attributes.
        marks = fields.get("mint_marks") or []
        mint_id = self.code_id(Mint, marks[0]) if marks else None
        if kind in {"coin", "bullion", "set", "medal", "token", "other", "unknown"}:
            self.session.add(CoinDetail(inventory_item_id=item.id, mint_id=mint_id))

    def _add_certification(self, item: InventoryItem, fields: dict) -> None:
        cert = fields.get("cert_number")
        if not cert:
            return
        # A comma-separated list is several certificates, not one string.
        for number in [c.strip() for c in str(cert).split(",") if c.strip()]:
            self.session.add(
                ItemCertification(
                    inventory_item_id=item.id,
                    cert_number=number,
                    raw=str(cert),
                )
            )

    def _grade_id(self, grade_raw: str | None, fields: dict) -> int | None:
        """Resolve a condition string to a grade, or decline to.

        Two things this must get right, and both were caught by looking at
        what a first pass actually produced:

        **Match the seeded vocabulary rather than beside it.** ``GEM/BU`` and
        the seeded code ``GEM_BU`` are the same grade written two ways, so
        matching is done on a key that ignores separators. Getting this wrong
        silently creates a second row for a concept that already exists, and
        every query then has to know about both.

        **A '+' is meaning, not noise.** ``UNC+`` is a real distinction from
        ``UNC`` and keeps its own row -- the owner said so explicitly -- so
        '+' survives into the key.

        **Not everything in a condition column is a condition.** Values like
        ``5-Coin Mint Set`` and ``Doubling (check P mark)`` are notes that
        ended up in that column. Inventing grade rows for them pollutes a
        vocabulary that is meant to be exportable to other installations, so
        they are declined, kept verbatim in ``grade_raw``, and recorded in
        ``attributes`` for review.
        """
        if not grade_raw:
            return None
        text = str(grade_raw).strip()
        if not text:
            return None

        parsed = parse_condition(text)

        # A condition string routinely carries several facts. Pulling each one
        # into its own column is what makes "every MS65-and-better Morgan" an
        # answerable question; storing the string whole makes it unanswerable.
        if parsed.designation:
            fields.setdefault("grade_designation", parsed.designation)
        if parsed.service:
            fields.setdefault("grading_service", parsed.service)
        if parsed.catalog_number:
            fields.setdefault("local_catalog_number", parsed.catalog_number)
        if parsed.seal_color:
            fields.setdefault("seal_color", parsed.seal_color)
        if parsed.note_attributes:
            fields.setdefault("note_attributes", list(parsed.note_attributes))

        # A value that named only a seal colour or a star note said nothing
        # about condition, and that is not a parse failure.
        if parsed.grade is None and (parsed.seal_color or parsed.note_attributes):
            return None

        if parsed.grade is None:
            fields["rating_unparsed"] = text
            return None

        index = self._grade_index()
        key = _grade_key(parsed.grade)
        if key in index:
            return index[key]

        row = Grade(
            code=parsed.grade, label=parsed.grade, source=ProvenanceSource.derived
        )
        self.session.add(row)
        self.session.flush()
        self.derived["grade"] = self.derived.get("grade", 0) + 1
        index[key] = row.id
        return row.id

    def _grade_index(self) -> dict[str, int]:
        """Normalised key -> grade id, over both codes and labels.

        Built once. Both are indexed because a source may write either the
        code (``MS64``) or the label (``MS-64``).
        """
        if self._grades is None:
            self._grades = {}
            for gid, code, label in self.session.execute(
                select(Grade.id, Grade.code, Grade.label)
            ).all():
                for variant in (code, label):
                    key = _grade_key(variant or "")
                    if key:
                        self._grades.setdefault(key, gid)
        return self._grades

    def _metal_and_fineness(
        self, bullion_form_id: int | None, fields: dict
    ) -> tuple[int | None, Decimal | None]:
        """Metal comes from the bullion product where the form implies one."""
        explicit = fields.get("metal")
        if explicit:
            return self.code_id(Metal, slug(str(explicit))), _as_decimal(
                fields.get("fineness")
            )
        if bullion_form_id is None:
            return None, _as_decimal(fields.get("fineness"))
        form = self.session.get(BullionForm, bullion_form_id)
        if form is None:
            return None, None
        return form.metal_id, form.typical_fineness

    def _fine_weight(
        self, gross: Decimal | None, fineness: Decimal | None, fields: dict
    ) -> Decimal | None:
        """Fine weight is gross times fineness -- the melt input.

        A Morgan dollar weighs 0.859380 ozt and contains 0.773440 ozt of
        silver. Using gross weight for melt overstates every 90% coin by 11%.
        """
        explicit = _as_decimal(fields.get("fine_weight_ozt"))
        if explicit is not None:
            return explicit
        if gross is None or fineness is None:
            return None
        return (gross * fineness).quantize(Decimal("0.000001"))

    def _denomination_id(self, fields: dict) -> int | None:
        """Match a face value to a seeded denomination.

        Matched on (currency, face value, kind) rather than on the written
        text, because the same face value is written a dozen ways. The kind
        matters: a US dollar exists as both a coin and a note, and they are
        different objects with different catalogues.

        Returns None when the source stated no face value. That is a real
        answer -- "Silver Eagle" has no meaningful face value for valuation --
        and it is better than attaching the item to a composition it does not
        have.
        """
        face = _as_decimal(fields.get("face_value"))
        kind = fields.get("denomination_kind")
        if face is None or kind is None:
            return None

        key = ("__denomination__", f"{kind}:{face}")
        if key in self._codes:
            return self._codes[key]

        usd = self.code_id(Currency, "USD")
        found = self.session.execute(
            select(Denomination.id).where(
                Denomination.currency_id == usd,
                Denomination.face_value == face,
                Denomination.kind == kind,
            )
        ).scalar_one_or_none()

        # Not created when missing: denominations are a curated catalogue of
        # what a mint actually issued, not a list of numbers seen in a file.
        self._codes[key] = found
        return found

    def _country_id(self, denomination_id: int | None) -> int | None:
        """Issuing country, inferred from the denomination rather than assumed.

        A USD denomination implies a US issuer. Nothing is guessed for items
        whose denomination did not resolve -- an unknown country would break
        the composition lookup in the direction of a wrong answer.
        """
        if denomination_id is None:
            return None
        return self.code_id(Country, "US")

    def resolve_composition(
        self, denomination_id: int | None, country_id: int | None, year: int | None
    ) -> Composition | None:
        """The public-fact lookup: what a coin of this denomination and year is made of.

        Returns None rather than guessing when the year is unknown.
        """
        if denomination_id is None or country_id is None or year is None:
            return None
        return self.session.execute(
            select(Composition)
            .where(
                Composition.denomination_id == denomination_id,
                Composition.country_id == country_id,
                Composition.year_from <= year,
                (Composition.year_to.is_(None)) | (Composition.year_to >= year),
            )
            .order_by(Composition.year_from.desc())
            .limit(1)
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        # str() first: never let a float become a money or weight value.
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _upper_or_none(value: object) -> str | None:
    return str(value).strip().upper() if value else None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"https?://([^/]+)", url)
    return match.group(1).lower() if match else None


#: Fields already stored in real columns; everything else a profile emits goes
#: to `attributes` rather than being silently dropped.
#:
#: These are the *profile's* names, which describe the spreadsheet, so they
#: keep saying `price` and `title` even though the columns they land in are
#: now `item_cost` and `source_title`. The mapping is explicit above.
_PROMOTED = frozenset(
    {
        "storage_form",
        "storage_quantity",
        "year_start",
        "year_end",
        "year_raw",
        "denom_raw",
        "grade_raw",
        "price",
        "shipping",
        "numismatic_value",
        "weight_ozt",
        "weight_raw",
        "weight_unit_raw",
        "status_marker",
        "serial_number",
        "cert_number",
        "mint_marks",
        "series_letter",
        "vendor_name",
        "vendor_url",
        "order_number",
        "ordered_on",
        "title",
        "description",
        "listing_url",
        "comment",
        "metal",
        "fineness",
        "fine_weight_ozt",
        "grading_service",
        "grade_designation",
        "face_value",
        "denomination_kind",
        "local_catalog_number",
        "seal_color",
        "note_attributes",
    }
)


def _attributes(fields: dict[str, Any]) -> dict[str, Any]:
    """The long tail: whatever the profile emitted that has no column yet.

    Anything here that starts being filtered or aggregated on has earned a
    real column -- that is the promotion rule, and this dict is where the
    evidence for it accumulates.
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _PROMOTED or value is None:
            continue
        out[key] = str(value) if isinstance(value, Decimal) else value
    return out


#: A grade key keeps letters, digits and '+', and drops every separator.
#: '+' survives because UNC+ is a different grade from UNC; separators do not,
#: because GEM/BU, GEM BU and GEM_BU are one grade written three ways.
_GRADE_DROP = re.compile(r"[^A-Z0-9+]")


def _grade_key(text: str) -> str:
    return _GRADE_DROP.sub("", str(text).upper())


@dataclass(frozen=True)
class ParsedCondition:
    """The separate facts a condition string was carrying."""

    grade: str | None = None
    designation: str | None = None
    service: str | None = None
    catalog_number: str | None = None
    #: A banknote's treasury seal colour, when the value named one.
    seal_color: str | None = None
    #: note_attribute codes -- features of the note, which are not grades.
    note_attributes: tuple[str, ...] = ()


#: Valid Sheldon numbers for each prefix. A grade is a point on a scale, not
#: any letter followed by any number: without this, prose yields "F73" and
#: "G63" and they become permanent rows in a shared vocabulary.
_GRADE_RANGE: dict[str, tuple[int, int]] = {
    "MS": (60, 70),
    "PR": (60, 70),
    "AU": (50, 58),
    "XF": (40, 45),
    "VF": (20, 35),
    "F": (12, 15),
    "VG": (8, 10),
    "G": (4, 6),
    "AG": (3, 3),
    "FR": (2, 2),
    "P": (1, 1),
}

#: Seal colours, which belong on currency_detail rather than in a grade.
_SEAL_COLORS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{name}\s+SEAL\b", re.I), name.lower())
    for name in ("BLUE", "RED", "BROWN", "GREEN", "GOLD")
)

#: Banknote features. Attributes of the note, never of its condition -- putting
#: these in the grade column is what makes condition unqueryable.
_NOTE_ATTRIBUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSTAR\s*NOTE\b|\bSTAR\b", re.I), "star"),
    (re.compile(r"\bFANCY\s*SERIAL\b", re.I), "fancy_serial"),
    (re.compile(r"\bCONSECUTIVE\b", re.I), "consecutive"),
    (re.compile(r"\bLOW\s*SERIAL\b", re.I), "low_serial"),
    (re.compile(r"\bSOLID\b", re.I), "solid_serial"),
    (re.compile(r"\bRADAR\b", re.I), "radar"),
    (re.compile(r"\bREPEATER\b", re.I), "repeater"),
    (re.compile(r"\bBINARY\b", re.I), "binary"),
    (re.compile(r"\bBIRTHDAY\b", re.I), "birthday"),
    (re.compile(r"\bWEB\s*PRESS\b", re.I), "web_press"),
)


#: An owner-assigned number, written as "#123" at the start of the value.
_CATALOG_NO = re.compile(r"#\s*(\d+)")

#: A Sheldon-style grade: a letter prefix, a number, optional plus signs.
#: EF is the British spelling of XF and normalises onto it.
_NUMERIC_GRADE = re.compile(
    # `\s*(?:-\s*)?` rather than `\s*-?\s*`: two optional whitespace runs
    # back to back are ambiguous, so a long run of spaces that never
    # completes a match backtracks super-linearly. This form accepts the
    # same strings with no ambiguity.
    r"\b(MS|PR|PF|AU|XF|EF|VF|VG|AG|FR|F|G|P)\s*(?:-\s*)?(\d{1,2})(\+*)",
    re.I,
)

#: Adjectival grades, longest first so "GEM BU" wins over a bare "BU" and
#: "GEM PROOF" wins over a bare "PROOF".
_ADJECTIVAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bCHOICE[\s/_-]*PROOF\b", re.I), "CHOICE_PROOF"),
    (re.compile(r"\bGEM[\s/_-]*PROOF\b", re.I), "GEM_PROOF"),
    (re.compile(r"\bCHOICE[\s/_-]*BU\b", re.I), "CHOICE_BU"),
    (re.compile(r"\bCHOICE[\s/_-]*UNC\b", re.I), "CHOICE_UNC"),
    (re.compile(r"\bGEM[\s/_-]*BU\b", re.I), "GEM_BU"),
    (re.compile(r"\bGEM[\s/_-]*UNC\b", re.I), "GEM_UNC"),
    (re.compile(r"\bPROOF\b", re.I), "PROOF"),
    (re.compile(r"\bBU\b", re.I), "BU"),
    (re.compile(r"\bUNC\b", re.I), "UNC"),
    (re.compile(r"\bCIRC\b", re.I), "CIRC"),
    # Bare letter grades, with no Sheldon number attached. Last, so an
    # "AU-55" is read as AU55 rather than collapsing onto plain AU.
    (re.compile(r"\bAU\b", re.I), "AU"),
    (re.compile(r"\b(?:XF|EF)\b", re.I), "XF"),
    (re.compile(r"\bVF\b", re.I), "VF"),
    (re.compile(r"\bVG\b", re.I), "VG"),
)

# No leading word boundary: in "PR69DCAM" the designation follows a digit, and
# \b never fires between two word characters. A negative lookbehind for a
# letter is the precise rule -- it still refuses to find CAM inside SCAM.
_DESIGNATION = re.compile(
    # The flag is scoped to the alternation instead of the whole pattern:
    # under a global re.I the lookbehind's [A-Za-z] is a duplicated class,
    # because each half already matches either case. Note (?i:...) does not
    # capture, so it sits inside a capturing group -- the caller reads
    # .group(1).
    r"(?<![A-Za-z])((?i:DCAM|DMPL|CAM|RD|RB|BN|FBL|FS|FB|FH|PL|EPQ|PPQ))\b"
)
_SERVICE = re.compile(r"\b(PCGS|NGC|ANACS|ICG|PMG|SEGS|CACG)\b", re.I)

#: PR and PF are the same thing written two ways; so are XF and EF.
_GRADE_PREFIX_ALIAS = {"PF": "PR", "EF": "XF"}


def parse_condition(text: str) -> ParsedCondition:
    """Decompose a condition string into the facts it actually carries.

    A real collection writes things like ``#14 PR69DCAM``, ``MS70 American
    Bald Eagle`` and ``Clad Roosevelt Gem Proof``. Each is a grade plus
    description, sometimes plus a designation, a grading service and the
    owner's own catalogue number.

    Extracting rather than accepting-or-rejecting the whole string matters in
    both directions. Rejecting wholesale leaves nearly half the collection
    with no grade at all. Accepting wholesale invents grades called
    ``ACADIANP`` and puts them in a vocabulary meant to be shared with other
    installations.

    ``grade`` is None when nothing grade-shaped is present, which is a real
    answer: the caller keeps the original text and flags it for a human.
    """
    value = str(text).strip()
    if not value:
        return ParsedCondition()

    catalog = _CATALOG_NO.search(value)
    designation = _DESIGNATION.search(value)
    service = _SERVICE.search(value)

    grade: str | None = None
    for match in _NUMERIC_GRADE.finditer(value):
        prefix = match.group(1).upper()
        prefix = _GRADE_PREFIX_ALIAS.get(prefix, prefix)
        number = int(match.group(2))
        low, high = _GRADE_RANGE.get(prefix, (0, 0))
        # Out-of-range means this was prose that happened to look like a
        # grade, not a grade. Keep scanning; a real one may follow.
        if low <= number <= high:
            grade = f"{prefix}{number}{match.group(3)}"
            break

    if grade is None:
        for pattern, code in _ADJECTIVAL:
            if found := pattern.search(value):
                # A trailing '+' is a real distinction and is preserved.
                tail = value[found.end() : found.end() + 2]
                plus = "".join(c for c in tail if c == "+")
                grade = code + plus
                break

    seal = next((c for p, c in _SEAL_COLORS if p.search(value)), None)
    attributes = tuple(c for p, c in _NOTE_ATTRIBUTES if p.search(value))

    return ParsedCondition(
        grade=grade,
        designation=designation.group(1).upper() if designation else None,
        service=service.group(1).upper() if service else None,
        catalog_number=catalog.group(1) if catalog else None,
        seal_color=seal,
        note_attributes=attributes,
    )
