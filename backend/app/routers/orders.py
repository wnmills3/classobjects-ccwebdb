"""Purchase flow. Customers create and read their own orders; admins see all.

Orders reference `listing`, which references `inventory_item` -- one foreign
key chain to either a coin or a banknote. That chain is what keeping a single
inventory table buys: with split coin/currency tables every order line would
need two nullable foreign keys and a constraint saying exactly one is set.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import (
    Customer,
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderChange,
    SalesOrderItem,
    SalesOrderStatus,
    SalesVenue,
    User,
    UserRole,
)
from ..order_writes import (
    Line,
    customer_for_user,
    payment_adjustment_due,
    place_order,
    record_status_change,
    return_stock,
    revise_order,
)
from ..references import require_code
from ..sales_venues import store_venue_id
from ..schemas import (
    OrderChangeOut,
    OrderCreate,
    OrderOut,
    OrderRevision,
    OrderStatusUpdate,
)
from ._resolve import found_or_404, get_or_404
from ._tx import commit, committing

router = APIRouter(prefix="/orders", tags=["orders"])

#: Statuses after which stock has left the building. Cancelling one of these
#: must not silently return stock that has already been posted.
SHIPPED_STATUSES = frozenset({"packed", "shipped", "delivered"})

# The same 404 whether the order does not exist or belongs to another
# customer, so order ids cannot be probed.
_ORDER_NOT_FOUND = "Order not found"


def _sold_as(line: SalesOrderItem) -> str:
    """What the line sold, as it was called then; today's name for older lines.

    **The offer's own wording first**, which is what the buyer was reading
    when they bought it. `routers.catalog.to_catalog_item` shows
    `listing.title or <the subject's own title>` in the shop, and this line
    is the same purchase seen afterwards: the two disagreeing means a buyer
    is shown one name on the page and another on their order.

    For a lot the difference is not cosmetic. `sales_lot.title` is the
    group's working name, chosen for the office -- the catalog deliberately
    keeps it out of the shop, which
    `test_a_lot_entry_shows_the_offer_wording_not_the_lots` pins -- and this
    function used to prefer it over the listing wording sitting beside it in
    the same snapshot.

    Falling back rather than choosing: an offer made with no wording of its
    own (`listing.title` is `""` by default) is named by what it sold, which
    for a lot line is the lot's title, because a lot snapshot has no `item`
    at all (`sale_snapshot`, version 2). Without that branch the last resort
    below reached `listing.inventory_item.source_title`, which is `None` for
    a lot listing -- an `AttributeError`, and a 500 on every page listing the
    order.
    """
    snapshot = line.item_snapshot or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    offered = snapshot.get("listing")
    offered_as = offered.get("title") if isinstance(offered, dict) else None
    if offered_as:
        return str(offered_as)
    item = snapshot.get("item")
    title = item.get("source_title") if isinstance(item, dict) else None
    if title:
        return str(title)
    lot = snapshot.get("lot")
    lot_title = lot.get("title") if isinstance(lot, dict) else None
    if lot_title:
        return str(lot_title)
    # A line whose snapshot predates lots and carries no title: fall back to
    # the listing, whose own title covers both shapes.
    return _listing_title(line.listing) or f"Listing #{line.listing_id}"


def _listing_title(listing: Listing | None) -> str | None:
    """A listing's name for a person: its item's title, or its lot's.

    The third reader of `listing.inventory_item.source_title` that a lot
    listing's NULL item would crash -- `order_changes` below builds its
    history rows from it.
    """
    if listing is None:
        return None
    item = listing.inventory_item
    if item is not None:
        return item.source_title
    lot = listing.sales_lot
    return lot.title if lot is not None else None


def _order_out(order: SalesOrder, status_code: str, *, for_admin: bool) -> OrderOut:
    """Build the API view of one order.

    `notes` (an administrator's internal remark) and `placed_by_email` (a
    staff address) are admin-only: a shopper's own order view carries
    neither, even though both fields stay in the response shape for every
    caller.
    """
    return OrderOut(
        id=order.id,
        customer_id=order.customer_id,
        customer_name=order.customer.display_name,
        customer_email=order.customer.email,
        sales_venue_code=order.sales_venue.code,
        sales_venue_name=order.sales_venue.name,
        status=status_code,
        total_amount=order.total_amount,
        placed_at=order.placed_at,
        items=[
            {
                "id": line.id,
                "listing_id": line.listing_id,
                "title": _sold_as(line),
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "snapshot": line.item_snapshot if for_admin else None,
                "listing_ended": line.listing is not None
                and line.listing.status is ListingStatus.ended,
            }
            for line in order.items
        ],
        version=order.version,
        notes=order.notes if for_admin else None,
        placed_by_email=(order.placed_by.email if order.placed_by else None)
        if for_admin
        else None,
        payment_adjustment_due=payment_adjustment_due(status_code, order.changes),
    )


#: What `_order_out` reads, loaded up front: a page of orders would otherwise
#: cost a query per order for its customer and two per line for its title.
_ORDER_DETAIL = (
    selectinload(SalesOrder.customer),
    selectinload(SalesOrder.sales_venue),
    selectinload(SalesOrder.items)
    .selectinload(SalesOrderItem.listing)
    .selectinload(Listing.inventory_item),
    # A lot line is titled by its lot (`_sold_as`).
    selectinload(SalesOrder.items)
    .selectinload(SalesOrderItem.listing)
    .selectinload(Listing.sales_lot),
    selectinload(SalesOrder.placed_by),
    selectinload(SalesOrder.changes),
)


def _status_code(db: Session, order: SalesOrder) -> str:
    # `get_one`: a status id naming no row is a broken database, not `pending`.
    return db.get_one(SalesOrderStatus, order.sales_order_status_id).code


def _load(db: Session, order_id: int) -> SalesOrder | None:
    return db.scalar(
        select(SalesOrder).where(SalesOrder.id == order_id).options(*_ORDER_DETAIL)
    )


def order_out(db: Session, order_id: int, *, for_admin: bool) -> OrderOut:
    """One order, freshly loaded, as the API returns it."""
    db.expire_all()
    order = _load(db, order_id)
    order = found_or_404(order, _ORDER_NOT_FOUND)
    return _order_out(order, _status_code(db, order), for_admin=for_admin)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: DbSession, user: CurrentUser) -> OrderOut:
    """Place an order, decrementing availability atomically.

    Routes through `order_writes.place_order`, which locks listings with
    SELECT ... FOR UPDATE and takes them in a stable id order, so two
    concurrent buyers can neither oversell the same listing nor deadlock
    against each other. This is verified by tests that drive the handler from
    real threads -- a test that serializes its requests would pass even with
    the lock removed.
    """
    customer = customer_for_user(db, user)
    order = place_order(
        db,
        customer,
        [Line(line.listing_id, line.quantity) for line in payload.items],
        placed_by=user,
    )
    db.commit()
    return order_out(db, order.id, for_admin=user.role is UserRole.manager)


@router.get("")
def list_orders(db: DbSession, user: CurrentUser, mine: bool = False) -> list[OrderOut]:
    """Customers see their own orders; administrators see every order.

    `mine=true` is the caller's own orders whatever their role. The shop's
    "Your orders" page asks for it: an administrator browsing the shop is a
    customer there, and everyone's orders belong in the console.
    """
    is_admin = user.role is UserRole.manager
    stmt = select(SalesOrder).options(*_ORDER_DETAIL).order_by(SalesOrder.id.desc())
    if mine or not is_admin:
        customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
        if customer is None:
            return []
        stmt = stmt.where(SalesOrder.customer_id == customer.id)

    orders = list(db.scalars(stmt).unique().all())
    return [
        _order_out(order, _status_code(db, order), for_admin=is_admin)
        for order in orders
    ]


def _visible_or_404(db: Session, order_id: int, user: User) -> SalesOrder:
    order = _load(db, order_id)
    order = found_or_404(order, _ORDER_NOT_FOUND)
    if user.role is not UserRole.manager:
        customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
        # 404 rather than 403 so ids of other customers' orders do not leak.
        if customer is None or order.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
            )
    return order


@router.get("/{order_id}")
def get_order(order_id: int, db: DbSession, user: CurrentUser) -> OrderOut:
    """One order. A customer sees only their own; an administrator sees any."""
    order = _visible_or_404(db, order_id, user)
    return _order_out(
        order, _status_code(db, order), for_admin=user.role is UserRole.manager
    )


@router.get("/{order_id}/changes")
def list_order_changes(
    order_id: int, db: DbSession, _admin: AdminUser
) -> list[OrderChangeOut]:
    """An order's history, newest first."""
    get_or_404(db, SalesOrder, order_id, _ORDER_NOT_FOUND)
    rows = db.scalars(
        select(SalesOrderChange)
        .where(SalesOrderChange.sales_order_id == order_id)
        .options(
            selectinload(SalesOrderChange.changed_by),
            selectinload(SalesOrderChange.listing).selectinload(Listing.inventory_item),
            selectinload(SalesOrderChange.listing).selectinload(Listing.sales_lot),
        )
        .order_by(SalesOrderChange.id.desc())
    ).all()
    return [
        OrderChangeOut(
            id=row.id,
            changed_at=row.changed_at,
            changed_by_email=row.changed_by.email if row.changed_by else None,
            change=row.change.value,
            listing_id=row.listing_id,
            listing_title=_listing_title(row.listing),
            from_value=row.from_value,
            to_value=row.to_value,
        )
        for row in rows
    ]


def _no_stock_to_return(db: Session, order: SalesOrder) -> str | None:
    """Why this order's stock could not be put back, or None when it can.

    Two shapes, one consequence, and the consequence is what the rule is
    about. `return_stock` adds each line's quantity back to its listing, and
    `order_writes._after_stock_change` moves the items off `sold` only when
    that listing is still active. Add stock back to a listing that has
    **ended** and neither happens: the listing carries phantom stock, the
    items stay `sold`, and `offering_writes._refuse_sold` then refuses to
    offer them ever again. The console has no remedy for either, which is why
    the transition is refused rather than half-performed.

    The first shape is a sale recorded from an outside platform:
    `sales_writes.record_sale` ended its listing when it recorded the sale.
    The second is a **lot** bought in the shop: `order_writes.place_order`
    ends that listing too, because a lot must end `sold` with its members
    released rather than stay `offered` for ever. The second shape did not
    exist until lots were sold in the shop, and it is reached by the most
    ordinary path there is -- buy a lot, cancel the order -- so it is asked
    about by listing status rather than left to the venue test, which a store
    order passes.

    Venue first, so an outside order keeps the message naming its platform:
    its listing is ended too, and the platform is the more useful news.

    Asked only of a cancellation that would really return stock: see the call
    site, which shares `return_stock`'s own `SHIPPED_STATUSES` condition.

    `paused` is deliberately not asked about. A listing whose stock an order
    holds cannot become paused afterwards -- pausing happens when the item is
    offered elsewhere, and `offering_writes._refuse_sold` refuses to offer a
    sold item -- so the only unactive status reachable here is `ended`.
    """
    if order.sales_venue_id != store_venue_id(db):
        venue = db.get(SalesVenue, order.sales_venue_id)
        return (
            f"records a sale on {venue.name if venue else 'another platform'}, "
            "whose listing was ended by the sale"
        )
    ended = list(
        db.scalars(
            select(Listing.id)
            .where(
                Listing.id.in_([item.listing_id for item in order.items]),
                Listing.status == ListingStatus.ended,
            )
            .order_by(Listing.id)
        ).all()
    )
    if ended:
        listings = ", ".join(f"#{listing_id}" for listing_id in ended)
        return (
            f"holds listing {listings}, which has ended -- a lot is sold as one "
            "group and its listing ends with the sale, so its stock cannot be "
            "put back on sale"
        )
    return None


@router.patch("/{order_id}")
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Advance an order. Cancelling an unshipped store one returns its stock.

    An **unshipped** order whose listing has already ended cannot be
    cancelled -- a sale recorded from an outside platform, or a lot bought in
    the shop -- because there is nothing to return the stock to
    (`_no_stock_to_return`). Once it has shipped, cancelling returns no stock
    and so strands nothing, and is how a refund is recorded.

    Locks and re-reads the `sales_order` row -- order first, listings second
    (inside `return_stock`), the same sequence `revise_order` uses -- so the
    two routes can never deadlock waiting on each other's locks.
    """
    order = db.execute(
        select(SalesOrder)
        .where(SalesOrder.id == order_id)
        .options(selectinload(SalesOrder.items))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    order = found_or_404(order, _ORDER_NOT_FOUND)

    previous = _status_code(db, order)
    # Cancelling an order that has not shipped returns its stock to the
    # catalog; a shipped order's goods have left, so cancelling it moves no
    # stock and is how a refund is recorded. Re-sending `cancelled` on an
    # already-cancelled order is a no-op.
    returns_stock = payload.status == "cancelled" and previous not in (
        SHIPPED_STATUSES | {"cancelled"}
    )
    # An order whose stock cannot be put back cannot be cancelled here --
    # `_no_stock_to_return` says which shape it is and why, and is where the
    # whole argument lives. Refusing the transition is the only option that
    # leaves nothing stranded; undoing such a sale needs a path that re-offers
    # what it sold, which nothing has yet.
    #
    # Asked only when this cancellation returns stock -- `returns_stock`,
    # the condition `return_stock` is called under below -- because nothing
    # can be stranded by a call that moves no stock: a shipped order is
    # cancellable whatever state its listing is in.
    if returns_stock:
        blocked = _no_stock_to_return(db, order)
        if blocked is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Order #{order_id} {blocked}. It cannot be cancelled here.",
            )
    # Cancelling an unshipped order put its stock back on sale. Moving it on
    # again would leave an order standing on stock already offered to the next
    # buyer -- the same coins sold twice. Re-sending "cancelled" stays harmless.
    if previous == "cancelled" and payload.status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order #{order_id} is cancelled and its stock has been "
            "returned. Place a new order instead.",
        )

    # The status write, its history row and (on a cancellation) returning
    # stock all sit inside `committing`, whose `StaleDataError` clause turns
    # a moved version into the 409 below. `return_stock` can autoflush a
    # write to `InventoryItem.disposition`, which carries its own version
    # column, so a concurrent edit to that item raised `StaleDataError` here
    # and not only at `db.commit()`. `return_stock` takes its rows through
    # `order_writes._lock_listings` and so through
    # `offering_writes.lock_for_sale`, which locks and re-reads every item it
    # will write, so this is defense in depth
    # (`test_a_concurrently_edited_item_no_longer_refuses_a_cancellation`).
    # The clause itself is still covered, by
    # `test_a_stale_data_error_inside_a_cancellation_is_a_409_not_a_500`,
    # which forces the failure from a patched flush at exactly the autoflush
    # this comment names: make that clause re-raise and that test goes red.
    # `order_id` (the path parameter), not `order.id`, appears in every
    # message below: once a flush has failed, every instance in the session
    # is expired, and reading an attribute off one issues a SELECT that
    # raises `PendingRollbackError` instead of the value.
    with committing(
        db,
        f"Order #{order_id} or one of its items was changed while saving. "
        "Reload and retry.",
    ):
        order.sales_order_status_id = require_code(
            db, SalesOrderStatus, payload.status, "status"
        )
        record_status_change(db, order, previous, payload.status, admin)

        if returns_stock:
            return_stock(db, order)
    return order_out(db, order_id, for_admin=True)


@router.put("/{order_id}")
def revise(
    order_id: int, payload: OrderRevision, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Replace an order's lines, prices, customer and notes, all or nothing."""
    order = _load(db, order_id)
    order = found_or_404(order, _ORDER_NOT_FOUND)
    customer = get_or_404(db, Customer, payload.customer_id, "No such customer")
    revise_order(
        db,
        order,
        customer=customer,
        lines=[Line(i.listing_id, i.quantity, i.unit_price) for i in payload.items],
        notes=payload.notes,
        version=payload.version,
        by=admin,
    )
    commit(db, f"Order #{order_id} was changed while saving. Reload and retry.")
    return order_out(db, order_id, for_admin=True)
