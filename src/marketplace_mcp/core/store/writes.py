"""Putting what Ozon answered into the store.

Every write is an upsert keyed on what Ozon itself keys on — a sku, an order
number, a parcel id — so a sync can be run twice, interrupted halfway, or
overlapped with another without duplicating anything. The one exception is
``price_observations``, which is an append: a second reading of the same price
is still a second reading, and it is what makes a history.
"""

import datetime as dt
import sqlite3
from typing import Any

from marketplace_mcp.adapters.ozon.models.catalog import Purchase, Tile
from marketplace_mcp.adapters.ozon.models.orders import Order, OrderDetail
from marketplace_mcp.core.utils.money import to_kopecks


def now() -> str:
    """The moment a reading was taken, as ISO-8601 in UTC."""
    return dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")


def save_items(connection: sqlite3.Connection, tiles: list[Tile] | list[Purchase], at: str) -> int:
    """Products, as the purchase history lists them.

    ``first_seen`` survives an update and ``last_seen`` moves, so an item that
    falls out of the history — Ozon drops products it no longer sells — keeps
    the date it was last actually there rather than looking current forever.
    """
    rows = [(tile.sku, tile.title, tile.image, tile.url, tile.seller, at, at) for tile in tiles if tile.sku]
    connection.executemany(
        """
        INSERT INTO items (sku, title, image, url, seller, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku) DO UPDATE SET
            title  = COALESCE(excluded.title, items.title),
            image  = COALESCE(excluded.image, items.image),
            url    = COALESCE(excluded.url, items.url),
            seller = COALESCE(excluded.seller, items.seller),
            last_seen = excluded.last_seen
        """,
        rows,
    )
    return len(rows)


def save_prices(connection: sqlite3.Connection, tiles: list[Tile] | list[Purchase], at: str) -> int:
    """One price reading per item, stamped with when it was read.

    Priced-less tiles are skipped rather than stored as null: "Ozon showed no
    price" and "the price was nothing" are different, and only the first ever
    happens.
    """
    rows = [(tile.sku, at, tile.price, to_kopecks(tile.price)) for tile in tiles if tile.sku and tile.price]
    connection.executemany(
        """
        INSERT INTO price_observations (sku, observed_at, price, kopecks)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(sku, observed_at) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def save_order(connection: sqlite3.Connection, order: Order, at: str) -> None:
    """The order as the list states it: status, its date, and what it is."""
    connection.execute(
        """
        INSERT INTO orders (order_number, state, status, status_date, synced_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(order_number) DO UPDATE SET
            state       = excluded.state,
            status      = excluded.status,
            status_date = COALESCE(excluded.status_date, orders.status_date),
            synced_at   = excluded.synced_at
        """,
        (order.order_number, str(order.state), order.status, order.date, at),
    )


def save_parcel(connection: sqlite3.Connection, detail: OrderDetail, at: str) -> None:
    """A parcel, its address, and the items that travelled in it.

    The order's total is written from here because this is where Ozon states
    it: the list of orders prints no total at all.
    """
    if not detail.order_number:
        return
    connection.execute(
        """
        UPDATE orders SET paid_total = ?, paid_kopecks = ?, payment_method = ?, synced_at = ?
        WHERE order_number = ?
        """,
        (detail.paid_total, to_kopecks(detail.paid_total), detail.payment_method, at, detail.order_number),
    )
    if not detail.shipment_id:
        return
    delivery = detail.delivery
    connection.execute(
        """
        INSERT INTO parcels (shipment_id, order_number, status, delivery_kind, delivery_address, recipient)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(shipment_id) DO UPDATE SET
            status           = excluded.status,
            delivery_kind    = COALESCE(excluded.delivery_kind, parcels.delivery_kind),
            delivery_address = COALESCE(excluded.delivery_address, parcels.delivery_address),
            recipient        = COALESCE(excluded.recipient, parcels.recipient)
        """,
        (
            detail.shipment_id,
            detail.order_number,
            detail.status,
            delivery.kind if delivery else None,
            delivery.address if delivery else None,
            detail.recipient,
        ),
    )
    connection.executemany(
        """
        INSERT INTO order_items
            (shipment_id, sku, order_number, title, variant, seller, price, price_kopecks, received)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shipment_id, sku) DO UPDATE SET
            title         = COALESCE(excluded.title, order_items.title),
            variant       = COALESCE(excluded.variant, order_items.variant),
            seller        = COALESCE(excluded.seller, order_items.seller),
            price         = COALESCE(excluded.price, order_items.price),
            price_kopecks = COALESCE(excluded.price_kopecks, order_items.price_kopecks),
            received      = excluded.received
        """,
        [
            (
                detail.shipment_id,
                product.sku,
                detail.order_number,
                product.title,
                product.variant,
                product.seller,
                product.price,
                to_kopecks(product.price),
                None if product.received is None else int(product.received),
            )
            for product in detail.products
            if product.sku
        ],
    )


def settled_orders(connection: sqlite3.Connection) -> set[str]:
    """Orders already stored whose outcome can no longer change.

    An incremental sync stops at one of these. Active orders are deliberately
    not in the set: their parcels move, so they are re-read every time however
    often they have been seen.
    """
    rows = connection.execute("SELECT order_number FROM orders WHERE state IN ('received', 'cancelled')")
    return {row["order_number"] for row in rows}


def start_run(connection: sqlite3.Connection, kind: str, at: str) -> None:
    connection.execute("INSERT INTO sync_runs (started_at, kind) VALUES (?, ?)", (at, kind))


def finish_run(connection: sqlite3.Connection, at: str, **counts: Any) -> None:
    fields = ", ".join(f"{name} = ?" for name in counts)
    connection.execute(
        f"UPDATE sync_runs SET finished_at = ?, {fields} WHERE started_at = ?",  # ruff: ignore[hardcoded-sql-expression] - names are literals here
        (now(), *counts.values(), at),
    )
