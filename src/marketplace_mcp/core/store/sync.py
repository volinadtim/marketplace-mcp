"""Bringing the store up to date with a marketplace.

Two passes, kept apart because they cost very different things.

Prices are one walk of the purchase list — a minute for a thousand items on
Ozon, cheap enough to run daily, and the only way a price history gets built at
all: a marketplace shows one price, the one right now.

Orders are a request per parcel, so a first sync of a few hundred orders takes
minutes. Every run after that stops as soon as it reaches an order already
stored and settled: the list arrives newest first, and an order that is received
or cancelled will not change again. Active orders are re-read whatever happens —
their parcels are still moving.

Neither pass knows which marketplace it is talking to. It is handed a source and
asks it three questions; everything site-shaped stopped at the adapter.
"""

import logging
import sqlite3
from dataclasses import dataclass, field

from marketplace_mcp.core.records import OrderState
from marketplace_mcp.core.source import MarketplaceSource
from marketplace_mcp.core.store import writes

logger = logging.getLogger(__name__)

PURCHASE_LIMIT = 1000
ORDER_LIMIT = 1000


@dataclass
class SyncReport:
    """What a sync did, in the terms a caller has to make decisions with."""

    marketplace: str
    kind: str
    items_seen: int = 0
    prices_written: int = 0
    orders_seen: int = 0
    orders_read: int = 0
    parcels_read: int = 0
    stopped_at: str | None = None
    """The stored order the walk stopped on, or None if it reached the end."""

    errors: list[str] = field(default_factory=list)


def sync_prices(
    connection: sqlite3.Connection,
    source: MarketplaceSource,
    limit: int = PURCHASE_LIMIT,
) -> SyncReport:
    """One walk of the purchase list: the catalogue, and a price reading.

    Note what the price is: what the site asks *today* for a product that may
    have been bought years ago. What was paid for it is a different number and
    comes from the order — this is the series, not the purchase.
    """
    at = writes.now()
    report = SyncReport(marketplace=source.name, kind="prices")
    writes.start_run(connection, source.name, report.kind, at)

    products = source.purchases(limit)
    report.items_seen = writes.save_items(connection, source.name, products, at)
    report.prices_written = writes.save_prices(connection, source.name, products, at)
    connection.commit()

    writes.finish_run(connection, source.name, at, items_seen=report.items_seen)
    connection.commit()
    return report


def sync_orders(
    connection: sqlite3.Connection,
    source: MarketplaceSource,
    *,
    full: bool = False,
    limit: int = ORDER_LIMIT,
) -> SyncReport:
    """Orders, their parcels, and what each parcel cost and where it went.

    ``full`` re-reads everything, for a first run or after a schema change.
    Without it the walk stops at the first order already stored and settled,
    which is what makes a routine run cheap.
    """
    at = writes.now()
    report = SyncReport(marketplace=source.name, kind="orders-full" if full else "orders")
    writes.start_run(connection, source.name, report.kind, at)
    settled = set() if full else writes.settled_orders(connection, source.name)

    for order in source.orders(limit):
        report.orders_seen += 1
        writes.save_order(connection, source.name, order, at)

        if order.number in settled:
            # Everything below this is older and settled too, so the walk is done.
            report.stopped_at = order.number
            break
        if order.state == OrderState.ACTIVE and not full:
            logger.debug("re-reading active order %s", order.number)

        try:
            parcels = source.parcels(order.number)
        except Exception as error:  # ruff: ignore[blind-except] - one bad order must not end the sync
            report.errors.append(f"{order.number}: {error}")
            logger.warning("could not read order %s: %s", order.number, error)
            continue

        for parcel in parcels:
            writes.save_parcel(connection, source.name, parcel, at)
            report.parcels_read += 1
        report.orders_read += 1
        connection.commit()

    connection.commit()
    writes.finish_run(
        connection,
        source.name,
        at,
        orders_seen=report.orders_seen,
        orders_read=report.orders_read,
        note="; ".join(report.errors[:5]) or None,
    )
    connection.commit()
    return report
