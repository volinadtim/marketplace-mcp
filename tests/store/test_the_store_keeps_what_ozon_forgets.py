"""The store answers the two questions a marketplace cannot.

A marketplace shows one price — the one right now — and states no history at
all. And it has no notion of "since last time", so a sync that cannot tell what
it already has re-reads a decade of orders every run.

Nothing here imports an adapter: the store is handed plain records, and these
tests are the check that it stayed that way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marketplace_mcp.core.records import (
    OrderRecord,
    OrderState,
    ParcelItemRecord,
    ParcelRecord,
    ProductRecord,
)
from marketplace_mcp.core.store import connect, writes

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

OZON = "ozon"
WB = "wildberries"


def _store(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "store.db")


def _product(sku: str, price: str | None) -> ProductRecord:
    return ProductRecord(sku=sku, title=f"Товар {sku}", price=price, image=f"https://ir.ozone.ru/{sku}.jpg")


def test_a_price_read_twice_is_two_readings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    writes.save_prices(store, OZON, [_product("1", "100 ₽")], "2026-09-01T10:00:00+00:00")
    writes.save_prices(store, OZON, [_product("1", "120 ₽")], "2026-09-08T10:00:00+00:00")
    rows = store.execute("SELECT observed_at, kopecks FROM price_observations ORDER BY observed_at").fetchall()
    assert [row["kopecks"] for row in rows] == [10000, 12000]


def test_a_priceless_product_is_not_a_price_of_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert writes.save_prices(store, OZON, [_product("1", None)], "2026-09-01T10:00:00+00:00") == 0
    assert store.execute("SELECT count(*) AS n FROM price_observations").fetchone()["n"] == 0


def test_first_seen_survives_but_last_seen_moves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    writes.save_items(store, OZON, [_product("1", "100 ₽")], "2026-09-01T10:00:00+00:00")
    writes.save_items(store, OZON, [_product("1", "120 ₽")], "2026-09-08T10:00:00+00:00")
    row = store.execute("SELECT first_seen, last_seen FROM items").fetchone()
    assert row["first_seen"].startswith("2026-09-01")
    assert row["last_seen"].startswith("2026-09-08")


def test_two_marketplaces_can_issue_the_same_number(tmp_path: Path) -> None:
    """A sku identifies a product only within the site that issued it. Without
    the marketplace in the key, whichever synced second would win.
    """
    store = _store(tmp_path)
    at = "2026-09-01T10:00:00+00:00"
    writes.save_items(store, OZON, [ProductRecord(sku="777", title="Гранола")], at)
    writes.save_items(store, WB, [ProductRecord(sku="777", title="Совсем другое")], at)
    rows = store.execute("SELECT marketplace, title FROM items ORDER BY marketplace").fetchall()
    assert [(row["marketplace"], row["title"]) for row in rows] == [(OZON, "Гранола"), (WB, "Совсем другое")]


def test_a_walk_stops_only_at_its_own_marketplace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    at = "2026-09-01T10:00:00+00:00"
    writes.save_order(store, OZON, OrderRecord(number="A-1", state=OrderState.RECEIVED), at)
    writes.save_order(store, WB, OrderRecord(number="A-2", state=OrderState.RECEIVED), at)
    assert writes.settled_orders(store, OZON) == {"A-1"}
    assert writes.settled_orders(store, WB) == {"A-2"}


def test_only_settled_orders_stop_an_incremental_walk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    at = "2026-09-01T10:00:00+00:00"
    for number, state in (("A-1", OrderState.RECEIVED), ("A-2", OrderState.CANCELLED), ("A-3", OrderState.ACTIVE)):
        writes.save_order(store, OZON, OrderRecord(number=number, state=state), at)
    assert writes.settled_orders(store, OZON) == {"A-1", "A-2"}


def test_one_sku_in_two_parcels_keeps_both_fates(tmp_path: Path) -> None:
    """Refusing an item at the pickup point cancels its parcel, not the order."""
    store = _store(tmp_path)
    at = "2026-09-01T10:00:00+00:00"
    writes.save_order(store, OZON, OrderRecord(number="A-1", state=OrderState.RECEIVED), at)
    for shipment, received in (("p1", True), ("p2", False)):
        writes.save_parcel(
            store,
            OZON,
            ParcelRecord(
                order_number="A-1",
                shipment_id=shipment,
                delivery_kind="Доставка курьером",
                delivery_address="Улица, 1",
                paid_total="695 ₽",
                items=[ParcelItemRecord(sku="777", title="Товар", price="219 ₽", received=received)],
            ),
            at,
        )
    rows = store.execute("SELECT shipment_id, received, price_kopecks FROM order_items ORDER BY shipment_id").fetchall()
    assert [(row["shipment_id"], row["received"]) for row in rows] == [("p1", 1), ("p2", 0)]
    assert rows[0]["price_kopecks"] == 21900
    assert store.execute("SELECT paid_kopecks FROM orders").fetchone()["paid_kopecks"] == 69500


def test_syncing_twice_does_not_duplicate_anything(tmp_path: Path) -> None:
    store = _store(tmp_path)
    at = "2026-09-01T10:00:00+00:00"
    for _ in range(2):
        writes.save_order(store, OZON, OrderRecord(number="A-1", state=OrderState.RECEIVED), at)
        writes.save_parcel(
            store,
            OZON,
            ParcelRecord(
                order_number="A-1",
                shipment_id="p1",
                items=[ParcelItemRecord(sku="777", title="Товар", price="219 ₽")],
            ),
            at,
        )
        writes.save_items(store, OZON, [_product("777", "219 ₽")], at)
    for table in ("orders", "parcels", "order_items", "items"):
        assert store.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 1  # ruff: ignore[hardcoded-sql-expression]
