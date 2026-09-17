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

SCHEMA_VERSION: Final = 1

# Money is kept twice on purpose: as Ozon rendered it, which is what a person
# recognises, and in kopecks, which is the only form that subtracts. Dates are
# ISO-8601 text — SQLite has no date type and ISO text sorts correctly.
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS items (
    sku          TEXT PRIMARY KEY,
    title        TEXT,
    image        TEXT,
    url          TEXT,
    seller       TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_number   TEXT PRIMARY KEY,
    state          TEXT,
    status         TEXT,
    status_date    TEXT,
    paid_total     TEXT,
    paid_kopecks   INTEGER,
    payment_method TEXT,
    synced_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parcels (
    shipment_id      TEXT PRIMARY KEY,
    order_number     TEXT NOT NULL REFERENCES orders(order_number) ON DELETE CASCADE,
    status           TEXT,
    delivery_kind    TEXT,
    delivery_address TEXT,
    recipient        TEXT
);

CREATE INDEX IF NOT EXISTS parcels_by_order ON parcels(order_number);

-- One row per item per parcel, never per order: the same sku can travel in two
-- parcels of one order and be received in one and refused in the other.
CREATE TABLE IF NOT EXISTS order_items (
    shipment_id  TEXT NOT NULL,
    sku          TEXT NOT NULL,
    order_number TEXT NOT NULL,
    title        TEXT,
    variant      TEXT,
    seller       TEXT,
    price        TEXT,
    price_kopecks INTEGER,
    received     INTEGER,
    PRIMARY KEY (shipment_id, sku)
);

CREATE INDEX IF NOT EXISTS order_items_by_sku ON order_items(sku);
CREATE INDEX IF NOT EXISTS order_items_by_order ON order_items(order_number);

-- A price with a time on it, one row per reading. This is the whole reason the
-- store exists: Ozon shows one price, the one right now.
CREATE TABLE IF NOT EXISTS price_observations (
    sku         TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price       TEXT,
    kopecks     INTEGER,
    PRIMARY KEY (sku, observed_at)
);

CREATE INDEX IF NOT EXISTS price_observations_by_sku ON price_observations(sku, observed_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    started_at  TEXT PRIMARY KEY,
    finished_at TEXT,
    kind        TEXT NOT NULL,
    orders_seen INTEGER DEFAULT 0,
    orders_read INTEGER DEFAULT 0,
    items_seen  INTEGER DEFAULT 0,
    note        TEXT
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

    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        msg = (
            f"{path} was written by schema version {version}, this is {SCHEMA_VERSION} — "
            "migrate it or point at a different file"
        )
        raise ValueError(msg)
    connection.commit()
    return connection
