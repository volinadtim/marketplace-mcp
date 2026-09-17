"""Two skus with one title are usually one product — and sometimes two.

Ozon reissues a card and the old sku stays in the purchase history: that is the
case worth linking. A size run and a colour range also share a title, and there
the two skus are two things owned separately — linking them deletes one from
the inventory. Every rule here exists to tell those apart, and each of them can
only refuse to link, never cause one.

The sku numbers in these fixtures are not decoration: Ozon numbers one
listing's variants together, so how far apart they are is evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marketplace_mcp.adapters.ozon.models.catalog import Purchase
from marketplace_mcp.adapters.ozon.models.enums import OrderState
from marketplace_mcp.adapters.ozon.models.orders import Order, OrderDetail, OrderProduct
from marketplace_mcp.core.store import connect, writes
from marketplace_mcp.core.store.dedup import ADJACENT, KEPT_APART, LINKED, SKIPPED, link_duplicates, normalise

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

AT = "2026-09-17T00:00:00+00:00"
OLD, NEW = "140119905", "1615718914"  # a reissue: a billion apart, years between them


def _store(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "store.db")


def _ordered(store: sqlite3.Connection, sku: str, title: str, variant: str | None, shipment: str) -> None:
    writes.save_order(store, Order(order_number="A-1", state=OrderState.RECEIVED), AT)
    writes.save_parcel(
        store,
        OrderDetail(
            order_number="A-1",
            shipment_id=shipment,
            products=[OrderProduct(sku=sku, title=title, variant=variant, price="100 ₽")],
        ),
        AT,
    )


def _methods(store: sqlite3.Connection) -> set[str]:
    return {row["method"] for row in store.execute("SELECT method FROM item_links")}


def test_a_reissued_card_is_linked_to_the_sku_that_has_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    writes.save_items(store, [Purchase(sku=OLD, title="Гранола Bionova, 400 г")], AT)
    writes.save_items(store, [Purchase(sku=NEW, title="Гранола BIONOVA, 400 г.")], AT)
    _ordered(store, NEW, "Гранола Bionova, 400 г", None, "p1")

    assert link_duplicates(store, AT)["skus_merged_away"] == 1
    rows = dict(store.execute("SELECT sku, canonical_sku FROM item_links").fetchall())
    assert rows[OLD] == NEW  # the one carrying both a card and an order
    assert rows[NEW] == NEW


def test_two_sizes_of_one_garment_stay_two_things(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for sku, variant, shipment in (
        ("1564640338", "46 RU / 31・Голубой", "p1"),
        ("2564640347", "48 RU / 32・Голубой", "p2"),
    ):
        writes.save_items(store, [Purchase(sku=sku, title="Шорты DARE, 1 шт")], AT)
        _ordered(store, sku, "Шорты DARE, 1 шт", variant, shipment)

    report = link_duplicates(store, AT)
    assert report["skus_merged_away"] == 0
    assert report["groups_kept_apart"] == 1
    rows = store.execute("SELECT sku, canonical_sku, note FROM item_links ORDER BY sku").fetchall()
    assert [row["sku"] for row in rows] == [row["canonical_sku"] for row in rows]
    assert "46 RU" in rows[0]["note"]  # why it was left alone, for a person to check


def test_one_colour_named_twice_is_still_one_product(tmp_path: Path) -> None:
    """A variant stated is not a variant that differs: a lens set sold only in
    black names a colour on both its skus and is one product all the same.
    """
    store = _store(tmp_path)
    for sku, shipment in ((OLD, "p1"), (NEW, "p2")):
        writes.save_items(store, [Purchase(sku=sku, title="Набор линз для телефона")], AT)
        _ordered(store, sku, "Набор линз для телефона", "Черный", shipment)

    assert link_duplicates(store, AT)["skus_merged_away"] == 1


def test_skus_numbered_together_are_a_listing_not_a_reissue(tmp_path: Path) -> None:
    """Nothing here states a variant, and they are still not one product: Ozon
    issued these six apart, which it does for the colours of one card. The
    title even says so — and no rule reads titles that closely.
    """
    store = _store(tmp_path)
    for sku in ("233673815", "233673821"):
        writes.save_items(store, [Purchase(sku=sku, title="Magazzino микрофон петличный серый черный")], AT)

    report = link_duplicates(store, AT)
    assert report["skus_merged_away"] == 0
    assert report["groups_adjacent_skus"] == 1
    assert _methods(store) == {ADJACENT}


def test_a_placeholder_is_not_a_name(tmp_path: Path) -> None:
    """Ozon prints this instead of a title for a product it will not show, and
    unrelated products then share it.
    """
    store = _store(tmp_path)
    for sku in (OLD, NEW):
        writes.save_items(store, [Purchase(sku=sku, title="Недоступно в вашем регионе")], AT)

    report = link_duplicates(store, AT)
    assert report["groups_unidentifying_title"] == 1
    assert store.execute("SELECT count(*) AS n FROM item_links").fetchone()["n"] == 0


def test_a_bare_category_is_not_a_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for sku in (OLD, NEW):
        writes.save_items(store, [Purchase(sku=sku, title="Футболка")], AT)
    assert link_duplicates(store, AT)["groups_unidentifying_title"] == 1


def test_a_title_held_by_one_sku_is_not_a_group(tmp_path: Path) -> None:
    store = _store(tmp_path)
    writes.save_items(store, [Purchase(sku=OLD, title="Что-то одно")], AT)
    assert link_duplicates(store, AT)["skus_merged_away"] == 0
    assert store.execute("SELECT count(*) AS n FROM item_links").fetchone()["n"] == 0


def test_linking_twice_does_not_reverse_the_arrows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for sku in (OLD, NEW):
        writes.save_items(store, [Purchase(sku=sku, title="Гранола Bionova")], AT)
    link_duplicates(store, AT)
    before = dict(store.execute("SELECT sku, canonical_sku FROM item_links").fetchall())
    link_duplicates(store, AT)
    assert dict(store.execute("SELECT sku, canonical_sku FROM item_links").fetchall()) == before
    assert _methods(store) == {LINKED}


def test_normalise_drops_what_a_reissue_rewrites() -> None:
    assert normalise("Гранола BIONOVA, ягодная, 1 шт") == normalise("гранола bionova ягодная")
    assert normalise("Шорты DARE") != normalise("Шорты DARK")


def test_every_verdict_is_recorded_under_its_own_name() -> None:
    assert len({LINKED, KEPT_APART, ADJACENT, SKIPPED}) == 4
