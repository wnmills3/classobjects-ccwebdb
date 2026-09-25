"""The database schema.

Importing this package registers every table on ``Base.metadata``, which is
what Alembic autogenerate diffs against and what ``create_all`` builds. Adding
a module here without importing it below means the table exists in Python and
nowhere else.

Layout follows `docs/database-design.md`:

    base            column conventions shared by everything
    reference       the classifier tables (design section 4)
    core            inventory_item and the acquisition side (section 3)
    identification  certificates, serials and type catalogs (section 5)
    valuation       composition, spot prices, snapshots (section 6)
    lifecycle       status and location history (section 7)
    images          files, derivatives and their uses (section 8)
    sales           listings, customers, orders, shipments (section 9)
    auctions        consigning lots to a sale, and settling it (section 9)
    scaffold        the users table, all that remains of the demo
"""

from __future__ import annotations

from .auctions import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
)
from .base import (
    Base,
    ProvenanceSource,
    ReferenceMixin,
    TimestampMixin,
    enum_column,
    utcnow,
)
from .core import (
    CoinDetail,
    CurrencyDetail,
    InventoryItem,
    PurchaseOrder,
    Vendor,
)
from .identification import (
    FriedbergNumber,
    ItemAttributeLink,
    ItemCertification,
    ItemError,
    PcgsType,
)
from .images import (
    DerivativeKind,
    Image,
    ImageDerivative,
    ItemImage,
    ListingImage,
    ShipmentImage,
    ShipmentImageKind,
)
from .lifecycle import (
    ItemFieldChange,
    ItemFieldReview,
    ItemFieldSource,
    ItemStatusHistory,
    LocationHistory,
    StorageLocation,
)
from .reference import (
    AppliesTo,
    AttributeGroup,
    Authenticity,
    BullionForm,
    Carrier,
    Country,
    Currency,
    Denomination,
    DenominationKind,
    Disposition,
    ErrorType,
    FedDistrict,
    Grade,
    GradeDesignation,
    GradeScale,
    GradingService,
    ImageRole,
    ItemAttribute,
    ItemKind,
    ItemStatus,
    Metal,
    Mint,
    NoteIssue,
    NoteType,
    ReferenceAlias,
    ReferenceMerge,
    SalesOrderStatus,
    SalesVenueKind,
    SealColor,
    Series,
    SeriesAlias,
    SeriesYearRange,
    SetForm,
    ShipmentStatus,
    SignatureCombination,
    StorageForm,
    StorageLocationKind,
    StrikeType,
    ValuationBasis,
    VendorKind,
)
from .sales import (
    Address,
    AddressKind,
    ClaimState,
    Customer,
    Listing,
    ListingFormat,
    ListingStatus,
    ListingStatusHistory,
    OfferClaim,
    SalesFeeKind,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderFee,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesVenue,
    Shipment,
)
from .scaffold import User, UserRole
from .valuation import Composition, MetalPrice, ValuationSnapshot

#: Reference tables, in dependency order. Seeding, export **and the reference
#: API** all walk this list, so a new classifier table joins the process by
#: being added here -- and a table left out of it has no
#: `/api/reference/<table>` endpoint at all, which is how `sales_fee_kind`
#: reached the console with a vocabulary the dialog could never fetch.
#: `test_every_reference_table_is_registered` (`tests/test_reference.py`) is
#: what now catches the omission rather than a 404 in a browser.
#:
#: A table with no `backend/data/reference/<table>.json` is fine here:
#: `seeding.seed_all` skips a table with no rows, which is how
#: `sales_venue_kind` and `sales_fee_kind` -- both seeded by their migration
#: -- have always been listed without a seed file.
REFERENCE_MODELS: tuple[type[ReferenceMixin], ...] = (
    ItemKind,
    SetForm,
    StorageForm,
    Currency,
    Country,
    Denomination,
    Metal,
    BullionForm,
    Mint,
    # Before Series: a note design names the seal color that is evidence
    # for it, and the note class it belongs to.
    SealColor,
    NoteType,
    Series,
    GradeScale,
    StrikeType,
    Grade,
    GradeDesignation,
    GradingService,
    Authenticity,
    ItemAttribute,
    FedDistrict,
    SignatureCombination,
    ValuationBasis,
    ErrorType,
    ItemStatus,
    Disposition,
    StorageLocationKind,
    ImageRole,
    VendorKind,
    SalesVenueKind,
    Carrier,
    SalesOrderStatus,
    SalesFeeKind,
    ShipmentStatus,
)

__all__ = [
    "REFERENCE_MODELS",
    "Address",
    "AddressKind",
    "AppliesTo",
    "AttributeGroup",
    "Auction",
    "AuctionLot",
    "AuctionLotResult",
    "AuctionStatus",
    "Authenticity",
    "Base",
    "BullionForm",
    "Carrier",
    "ClaimState",
    "CoinDetail",
    "Composition",
    "Country",
    "Currency",
    "CurrencyDetail",
    "Customer",
    "Denomination",
    "DenominationKind",
    "DerivativeKind",
    "Disposition",
    "ErrorType",
    "FedDistrict",
    "FriedbergNumber",
    "Grade",
    "GradeDesignation",
    "GradeScale",
    "GradingService",
    "Image",
    "ImageDerivative",
    "ImageRole",
    "InventoryItem",
    "ItemAttribute",
    "ItemAttributeLink",
    "ItemCertification",
    "ItemError",
    "ItemFieldChange",
    "ItemFieldReview",
    "ItemFieldSource",
    "ItemImage",
    "ItemKind",
    "ItemStatus",
    "ItemStatusHistory",
    "Listing",
    "ListingFormat",
    "ListingImage",
    "ListingStatus",
    "ListingStatusHistory",
    "LocationHistory",
    "Metal",
    "MetalPrice",
    "Mint",
    "NoteIssue",
    "NoteType",
    "OfferClaim",
    "PcgsType",
    "ProvenanceSource",
    "PurchaseOrder",
    "ReferenceAlias",
    "ReferenceMerge",
    "ReferenceMixin",
    "SalesFeeKind",
    "SalesLot",
    "SalesLotItem",
    "SalesLotStatus",
    "SalesOrder",
    "SalesOrderChange",
    "SalesOrderChangeKind",
    "SalesOrderFee",
    "SalesOrderItem",
    "SalesOrderItemShare",
    "SalesOrderStatus",
    "SalesVenue",
    "SalesVenueKind",
    "SealColor",
    "Series",
    "SeriesAlias",
    "SeriesYearRange",
    "SetForm",
    "Shipment",
    "ShipmentImage",
    "ShipmentImageKind",
    "ShipmentStatus",
    "SignatureCombination",
    "StorageForm",
    "StorageLocation",
    "StorageLocationKind",
    "StrikeType",
    "TimestampMixin",
    "User",
    "UserRole",
    "ValuationBasis",
    "ValuationSnapshot",
    "Vendor",
    "VendorKind",
    "enum_column",
    "utcnow",
]
