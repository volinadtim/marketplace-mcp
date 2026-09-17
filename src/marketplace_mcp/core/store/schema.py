"""The local database: tables, and opening one.

Ozon is the source, not the record. Its own history is a catalogue of products
with today's prices on them — it does not say what an item cost when it was
bought, and the moment a price changes the old one is gone. Anything that wants
to look back has to keep its own copy, which is what this is.

SQLite rather than a file of JSON because two of the questions asked of it are
not document-shaped: what a price did over time, and what changed since the
last sync.
"""

import sqlite3
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 2

# Every row carries the marketplace it came from, and it is part of the key: a
# sku only identifies a product within the site that issued it, and two
# marketplaces hand out the same digits sooner or later. On a bare sku,
# Wildberries would silently overwrite an Ozon product sharing its number.
#
# Money is kept twice on purpose: as the site rendered it, which is what a
# person recognises, and in kopecks, which is the only form that subtracts.
# Dates are ISO-8601 text — SQLite has no date type and ISO text sorts
# correctly.
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS items (
    marketplace  TEXT NOT NULL,
    sku          TEXT NOT NULL,
    title        TEXT,
    image        TEXT,
    url          TEXT,
    seller       TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (marketplace, sku)
);

CREATE TABLE IF NOT EXISTS orders (
    marketplace    TEXT NOT NULL,
    order_number   TEXT NOT NULL,
    state          TEXT,
    status         TEXT,
    status_date    TEXT,
    paid_total     TEXT,
    paid_kopecks   INTEGER,
    payment_method TEXT,
    synced_at      TEXT NOT NULL,
    PRIMARY KEY (marketplace, order_number)
);

CREATE TABLE IF NOT EXISTS parcels (
    marketplace      TEXT NOT NULL,
    shipment_id      TEXT NOT NULL,
    order_number     TEXT NOT NULL,
    status           TEXT,
    delivery_kind    TEXT,
    delivery_address TEXT,
    recipient        TEXT,
    PRIMARY KEY (marketplace, shipment_id),
    FOREIGN KEY (marketplace, order_number)
        REFERENCES orders(marketplace, order_number) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS parcels_by_order ON parcels(marketplace, order_number);

-- One row per item per parcel, never per order: the same sku can travel in two
-- parcels of one order and be received in one and refused in the other.
CREATE TABLE IF NOT EXISTS order_items (
    marketplace  TEXT NOT NULL,
    shipment_id  TEXT NOT NULL,
    sku          TEXT NOT NULL,
    order_number TEXT NOT NULL,
    title        TEXT,
    variant      TEXT,
    seller       TEXT,
    price        TEXT,
    price_kopecks INTEGER,
    received     INTEGER,
    PRIMARY KEY (marketplace, shipment_id, sku)
);

CREATE INDEX IF NOT EXISTS order_items_by_sku ON order_items(marketplace, sku);
CREATE INDEX IF NOT EXISTS order_items_by_order ON order_items(marketplace, order_number);

-- A price with a time on it, one row per reading. This is the whole reason the
-- store exists: Ozon shows one price, the one right now.
CREATE TABLE IF NOT EXISTS price_observations (
    marketplace TEXT NOT NULL,
    sku         TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price       TEXT,
    kopecks     INTEGER,
    PRIMARY KEY (marketplace, sku, observed_at)
);

CREATE INDEX IF NOT EXISTS price_observations_by_sku ON price_observations(marketplace, sku, observed_at);

-- The same product under two skus. Ozon reissues a card and the old sku stays
-- in the purchase history, so one thing bought twice looks like two things.
-- A link, never a merge: the rows keep their own prices and orders, and a
-- judgement that turns out wrong is undone by deleting a row here.
CREATE TABLE IF NOT EXISTS item_links (
    marketplace   TEXT NOT NULL,
    sku           TEXT NOT NULL,
    canonical_sku TEXT NOT NULL,
    method        TEXT NOT NULL,
    note          TEXT,
    linked_at     TEXT NOT NULL,
    PRIMARY KEY (marketplace, sku)
);

CREATE INDEX IF NOT EXISTS item_links_by_canonical ON item_links(marketplace, canonical_sku);

CREATE TABLE IF NOT EXISTS sync_runs (
    started_at  TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    finished_at TEXT,
    kind        TEXT NOT NULL,
    orders_seen INTEGER DEFAULT 0,
    orders_read INTEGER DEFAULT 0,
    items_seen  INTEGER DEFAULT 0,
    note        TEXT,
    PRIMARY KEY (marketplace, started_at)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the store, creating it if it is not there yet.

    Foreign keys are switched on per connection — SQLite defaults them off — and
    WAL is set so a long sync does not lock out a reader looking at the data it
    has written so far.
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(_SCHEMA)

    _bring_up_to_date(connection, path)
    connection.commit()
    return connection


# The tables that gained a marketplace column in version 2, and the columns each
# of them had before it. Listed rather than read back from the database because
# a migration has to know what it is migrating *from*, not what it finds.
_V1_TABLES: Final = {
    "items": "sku, title, image, url, seller, first_seen, last_seen",
    "orders": "order_number, state, status, status_date, paid_total, paid_kopecks, payment_method, synced_at",
    "parcels": "shipment_id, order_number, status, delivery_kind, delivery_address, recipient",
    "order_items": "shipment_id, sku, order_number, title, variant, seller, price, price_kopecks, received",
    "price_observations": "sku, observed_at, price, kopecks",
    "item_links": "sku, canonical_sku, method, note, linked_at",
    "sync_runs": "started_at, finished_at, kind, orders_seen, orders_read, items_seen, note",
}


def _bring_up_to_date(connection: sqlite3.Connection, path: Path) -> None:
    """Move an existing store to the current schema, or refuse to guess.

    A store opened by the version that wrote it is left alone. A store from
    version 1 is migrated: everything in it came from Ozon, since that was the
    only marketplace then, so the column added is filled with that rather than
    left for the owner to work out. Anything else is refused — a store from a
    *newer* version has fields this code would drop on write.
    """
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version == 0:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    if version == 1:
        _add_the_marketplace_column(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    msg = f"{path} was written by schema version {version}, this is {SCHEMA_VERSION} — point at a different file"
    raise ValueError(msg)


def _add_the_marketplace_column(connection: sqlite3.Connection) -> None:
    """Version 1 to 2: every row gains the marketplace it came from.

    SQLite cannot add a column to a primary key in place, so each table is
    rebuilt: the old one is renamed aside, the new shape is created by the
    schema script above, the rows are copied through with "ozon" filled in, and
    the old one is dropped. Foreign keys are off for the duration — mid-rebuild
    the parcels table points at an orders table that is briefly not there — and
    the whole thing is one transaction, so an interruption leaves the store as
    it was rather than half-converted.
    """
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:  # commits, or rolls the whole rebuild back
            present = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in _V1_TABLES:
                if table in present:
                    connection.execute(f"ALTER TABLE {table} RENAME TO {table}_v1")
            connection.executescript(_SCHEMA)
            for table, columns in _V1_TABLES.items():
                if table in present:
                    connection.execute(
                        f"INSERT INTO {table} (marketplace, {columns}) SELECT 'ozon', {columns} FROM {table}_v1"
                    )
                    connection.execute(f"DROP TABLE {table}_v1")
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
