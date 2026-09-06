"""The database schema.

Importing this package registers every table on ``Base.metadata``, which is
what Alembic autogenerate diffs against and what ``create_all`` builds. Adding
a module here without importing it below means the table exists in Python and
nowhere else.

Layout follows `docs/database-design.md`:

    base            column conventions shared by everything
    reference       the classifier tables (design section 4)
    core            inventory_item and the acquisition side (section 3)
    identification  certificates, serials and type catalogues (section 5)
    valuation       composition, spot prices, snapshots (section 6)
    lifecycle       status and location history (section 7)
    images          files, derivatives and their uses (section 8)
    sales           listings, customers, orders, shipments (section 9)
    scaffold        the superseded storefront demo
"""

from __future__ import annotations

from .base import (
    Base,
    ProvenanceSource,
    ReferenceMixin,
    TimestampMixin,
    enum_column,
    utcnow,
)
from .core import (
    DEFAULT_TAX_RATE,
    CoinDetail,
    CurrencyDetail,
    InventoryItem,
    PurchaseOrder,
    Vendor,
)
from .identification import (
    FriedbergNumber,
    ItemCertification,
    ItemNoteAttribute,
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
from .lifecycle import ItemStatusHistory, LocationHistory, StorageLocation
from .reference import (
    AppliesTo,
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
    ItemKind,
    ItemStatus,
    Metal,
    Mint,
    NoteAttribute,
    NoteType,
    SalesOrderStatus,
    SealColor,
    SetForm,
    ShipmentStatus,
    SignatureCombination,
    StorageForm,
    StorageLocationKind,
    ValuationBasis,
    VendorKind,
)
from .sales import (
    Address,
    AddressKind,
    Customer,
    Listing,
    SalesOrder,
    SalesOrderItem,
    Shipment,
)
from .scaffold import Coin, CoinKind, Order, OrderItem, OrderStatus, User, UserRole
from .valuation import Composition, MetalPrice, ValuationSnapshot

#: Reference tables, in dependency order. Seeding and export both walk this
#: list, so a new classifier table joins the process by being added here.
REFERENCE_MODELS: tuple[type, ...] = (
    ItemKind,
    SetForm,
    StorageForm,
    Currency,
    Country,
    Denomination,
    Metal,
    BullionForm,
    Mint,
    GradeScale,
    Grade,
    GradeDesignation,
    GradingService,
    Authenticity,
    NoteType,
    NoteAttribute,
    SealColor,
    FedDistrict,
    SignatureCombination,
    ValuationBasis,
    ErrorType,
    ItemStatus,
    Disposition,
    StorageLocationKind,
    ImageRole,
    VendorKind,
    Carrier,
    SalesOrderStatus,
    ShipmentStatus,
)

__all__ = [
    "DEFAULT_TAX_RATE",
    "REFERENCE_MODELS",
    "Address",
    "AddressKind",
    "AppliesTo",
    "Authenticity",
    "Base",
    "BullionForm",
    "Carrier",
    "Coin",
    "CoinDetail",
    "CoinKind",
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
    "ItemCertification",
    "ItemKind",
    "ItemImage",
    "ItemNoteAttribute",
    "ItemStatus",
    "ItemStatusHistory",
    "Listing",
    "ListingImage",
    "LocationHistory",
    "Metal",
    "MetalPrice",
    "Mint",
    "NoteAttribute",
    "NoteType",
    "Order",
    "OrderItem",
    "OrderStatus",
    "PcgsType",
    "ProvenanceSource",
    "PurchaseOrder",
    "ReferenceMixin",
    "SalesOrder",
    "SalesOrderItem",
    "SalesOrderStatus",
    "SealColor",
    "SetForm",
    "Shipment",
    "ShipmentImage",
    "ShipmentImageKind",
    "ShipmentStatus",
    "SignatureCombination",
    "StorageForm",
    "StorageLocation",
    "StorageLocationKind",
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
