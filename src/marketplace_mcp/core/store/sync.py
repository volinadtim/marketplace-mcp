"""Bringing the store up to date with the account.

Two passes, kept apart because they cost very different things.

Prices are one walk of the purchase history — a minute for a thousand items,
cheap enough to run daily, and the only way a price history gets built.

Orders are a request per parcel, so a first sync of a few hundred orders takes
tens of minutes. Every run after that stops as soon as it reaches an order
already stored and settled: the list arrives newest first, and an order that is
received or cancelled will not change again. Active orders are re-read whatever
happens — their parcels are still moving.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from marketplace_mcp.adapters.ozon.models.enums import OrderState
from marketplace_mcp.adapters.ozon.services import catalog, orders as orders_service
from marketplace_mcp.core.store import writes

logger = logging.getLogger(__name__)

PURCHASE_LIMIT = 1000
ORDER_LIMIT = 1000


@dataclass
class SyncReport:
    """What a sync did, in the terms a caller has to make decisions with."""

    kind: str
    items_seen: int = 0
    prices_written: int = 0
    orders_seen: int = 0
    orders_read: int = 0
    parcels_read: int = 0
    stopped_at: str | None = field(
        default=None,
        metadata={"doc": "The stored order the walk stopped on, or None if it reached the end."},
    )
    errors: list[str] = field(default_factory=list)


def sync_prices(connection: Any, limit: int = PURCHASE_LIMIT) -> SyncReport:
    """One walk of the purchase history: the catalogue, and a price reading.

    Note what the price is: Ozon's price *today*, on a product that may have
    been bought years ago. What it cost when it was bought is a different
    number and comes from the order — this is the series, not the purchase.
    """
    at = writes.now()
    report = SyncReport(kind="prices")
    writes.start_run(connection, report.kind, at)

    tiles = catalog.purchases(limit=limit)
    report.items_seen = writes.save_items(connection, tiles, at)
    report.prices_written = writes.save_prices(connection, tiles, at)
    connection.commit()

    writes.finish_run(connection, at, items_seen=report.items_seen)
    connection.commit()
    return report


def sync_orders(connection: Any, *, full: bool = False, limit: int = ORDER_LIMIT) -> SyncReport:
    """Orders, their parcels, and what each parcel cost and where it went.

    ``full`` re-reads everything, for a first run or after a schema change.
    Without it the walk stops at the first order already stored and settled,
    which is what makes a routine run cheap.
    """
    at = writes.now()
    report = SyncReport(kind="orders-full" if full else "orders")
    writes.start_run(connection, report.kind, at)
    settled = set() if full else writes.settled_orders(connection)

    for order in orders_service.list_orders("all", limit):
        if not order.order_number:
            continue
        report.orders_seen += 1
        writes.save_order(connection, order, at)

        if order.order_number in settled:
            # Everything below this is older and settled too, so the walk is done.
            report.stopped_at = order.order_number
            break
        if order.state == OrderState.ACTIVE and not full:
            logger.debug("re-reading active order %s", order.order_number)

        try:
            parcels = orders_service.order_parcels(order.order_number)
        except Exception as error:  # ruff: ignore[blind-except] - one bad order must not end the sync
            report.errors.append(f"{order.order_number}: {error}")
            logger.warning("could not read order %s: %s", order.order_number, error)
            continue

        for parcel in parcels:
            writes.save_parcel(connection, parcel, at)
            report.parcels_read += 1
        report.orders_read += 1
        connection.commit()

    connection.commit()
    writes.finish_run(
        connection,
        at,
        orders_seen=report.orders_seen,
        orders_read=report.orders_read,
        note="; ".join(report.errors[:5]) or None,
    )
    connection.commit()
    return report
