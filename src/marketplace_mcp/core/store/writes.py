"""Putting what a marketplace answered into the store.

Every write is an upsert keyed on what the marketplace itself keys on — a sku,
an order number, a parcel id, each within the marketplace it came from — so a
sync can be run twice, interrupted halfway, or overlapped with another without
duplicating anything. The one exception is ``price_observations``, which is an
append: a second reading of the same price is still a second reading, and it is
what makes a history.
"""

import datetime as dt
import sqlite3

from marketplace_mcp.core.records import OrderRecord, ParcelRecord, ProductRecord
from marketplace_mcp.core.utils.money import to_kopecks


def now() -> str:
    """The moment a reading was taken, as ISO-8601 in UTC."""
    return dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")


def save_items(connection: sqlite3.Connection, marketplace: str, products: list[ProductRecord], at: str) -> int:
    """Products, as a marketplace's own list of purchases shows them.

    ``first_seen`` survives an update and ``last_seen`` moves, so a product that
    falls out of the list — sites drop what they no longer sell — keeps the date
    it was last actually there instead of looking current forever. It is not
    deleted: a thing bought two years ago is not less bought for having been
    delisted since.
    """
    rows = [
        (marketplace, item.sku, item.title, item.image, item.url, item.seller, at, at) for item in products if item.sku
    ]
    connection.executemany(
        """
        INSERT INTO items (marketplace, sku, title, image, url, seller, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, sku) DO UPDATE SET
            title  = COALESCE(excluded.title, items.title),
            image  = COALESCE(excluded.image, items.image),
            url    = COALESCE(excluded.url, items.url),
            seller = COALESCE(excluded.seller, items.seller),
            last_seen = excluded.last_seen
        """,
        rows,
    )
    return len(rows)


def save_prices(connection: sqlite3.Connection, marketplace: str, products: list[ProductRecord], at: str) -> int:
    """One price reading per product, stamped with when it was read.

    Products with no price are skipped rather than stored as null: "the site
    showed no price" and "the price was nothing" are different, and only the
    first ever happens.
    """
    rows = [
        (marketplace, item.sku, at, item.price, to_kopecks(item.price)) for item in products if item.sku and item.price
    ]
    connection.executemany(
        """
        INSERT INTO price_observations (marketplace, sku, observed_at, price, kopecks)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, sku, observed_at) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def save_order(connection: sqlite3.Connection, marketplace: str, order: OrderRecord, at: str) -> None:
    """The order as its list states it: what became of it, and when."""
    connection.execute(
        """
        INSERT INTO orders (marketplace, order_number, state, status, status_date, synced_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, order_number) DO UPDATE SET
            state       = excluded.state,
            status      = excluded.status,
            status_date = COALESCE(excluded.status_date, orders.status_date),
            synced_at   = excluded.synced_at
        """,
        (marketplace, order.number, str(order.state), order.status, order.status_date, at),
    )


def save_parcel(connection: sqlite3.Connection, marketplace: str, parcel: ParcelRecord, at: str) -> None:
    """A parcel, where it went, and the items that travelled in it.

    The order's total is written from here because this is where the sites
    state it: a list of orders prints no total at all.
    """
    if not parcel.order_number:
        return
    connection.execute(
        """
        UPDATE orders SET paid_total = ?, paid_kopecks = ?, payment_method = ?, synced_at = ?
        WHERE marketplace = ? AND order_number = ?
        """,
        (
            parcel.paid_total,
            to_kopecks(parcel.paid_total),
            parcel.payment_method,
            at,
            marketplace,
            parcel.order_number,
        ),
    )
    if not parcel.shipment_id:
        return
    connection.execute(
        """
        INSERT INTO parcels
            (marketplace, shipment_id, order_number, status, delivery_kind, delivery_address, recipient)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, shipment_id) DO UPDATE SET
            status           = excluded.status,
            delivery_kind    = COALESCE(excluded.delivery_kind, parcels.delivery_kind),
            delivery_address = COALESCE(excluded.delivery_address, parcels.delivery_address),
            recipient        = COALESCE(excluded.recipient, parcels.recipient)
        """,
        (
            marketplace,
            parcel.shipment_id,
            parcel.order_number,
            parcel.status,
            parcel.delivery_kind,
            parcel.delivery_address,
            parcel.recipient,
        ),
    )
    connection.executemany(
        """
        INSERT INTO order_items
            (marketplace, shipment_id, sku, order_number, title, variant, seller, price, price_kopecks, received)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, shipment_id, sku) DO UPDATE SET
            title         = COALESCE(excluded.title, order_items.title),
            variant       = COALESCE(excluded.variant, order_items.variant),
            seller        = COALESCE(excluded.seller, order_items.seller),
            price         = COALESCE(excluded.price, order_items.price),
            price_kopecks = COALESCE(excluded.price_kopecks, order_items.price_kopecks),
            received      = excluded.received
        """,
        [
            (
                marketplace,
                parcel.shipment_id,
                item.sku,
                parcel.order_number,
                item.title,
                item.variant,
                item.seller,
                item.price,
                to_kopecks(item.price),
                None if item.received is None else int(item.received),
            )
            for item in parcel.items
            if item.sku
        ],
    )


def settled_orders(connection: sqlite3.Connection, marketplace: str) -> set[str]:
    """Orders already stored whose outcome can no longer change.

    An incremental sync stops at one of these. Active orders are deliberately
    not in the set: their parcels move, so they are re-read every time however
    often they have been seen.
    """
    rows = connection.execute(
        "SELECT order_number FROM orders WHERE marketplace = ? AND state IN ('received', 'cancelled')",
        (marketplace,),
    )
    return {row["order_number"] for row in rows}


def start_run(connection: sqlite3.Connection, marketplace: str, kind: str, at: str) -> None:
    connection.execute(
        "INSERT INTO sync_runs (marketplace, started_at, kind) VALUES (?, ?, ?)",
        (marketplace, at, kind),
    )


def finish_run(connection: sqlite3.Connection, marketplace: str, at: str, **counts: object) -> None:
    # The column names come from the caller's keywords, which are literals at
    # every call site; SQLite takes no parameter in the place of a column name.
    fields = ", ".join(f"{name} = ?" for name in counts)
    statement = f"UPDATE sync_runs SET finished_at = ?, {fields} WHERE marketplace = ? AND started_at = ?"  # ruff: ignore[hardcoded-sql-expression]
    connection.execute(statement, (now(), *counts.values(), marketplace, at))
