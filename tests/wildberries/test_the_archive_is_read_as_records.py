"""Turning WB's order archive into what the store keeps.

One endpoint answers with a row per item, and the three reads the core asks for
are three views of it. The judgements worth pinning down are what counts as
received, what an order's state is when its lines disagree, and what a parcel
even is on a site that names none.
"""

from __future__ import annotations

from typing import Any

import pytest

from marketplace_mcp.adapters.wildberries.source import WildberriesSource
from marketplace_mcp.core.records import OrderState
from marketplace_mcp.core.source import MarketplaceSource


class FakeSession:
    """Answers with one scripted archive, and counts how often it is asked."""

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self.lines = lines
        self.calls = 0

    def post(self, _url: str, _body: Any = None) -> dict[str, Any]:
        self.calls += 1
        return {"value": {"archive": self.lines}}


class NoImages:
    def find(self, _nm_id: int) -> str | None:
        return None


class KnownOffices:
    ADDRESSES = {211648: "Санкт-Петербург, Белорусская Улица 4"}

    def address(self, office_id: int | None) -> str | None:
        return self.ADDRESSES.get(office_id or 0)

    def kind(self, office_id: int | None) -> str | None:
        return "Доставка курьером" if (office_id or 0) >= 50_000_000 else "Доставка в пункт выдачи"


def line(**fields: Any) -> dict[str, Any]:
    base = {
        "orderId": "order-1",
        "code1S": 868697458,
        "name": "Shorts",
        "brand": "ОБОЮДНО",
        "size": "M",
        "color": "blue",
        "rawPrice": 161200,
        "status": "Purchased",
        "orderDate": "2026-07-14T19:41:52Z",
        "lastDate": "2026-07-16T17:09:16Z",
        "officeId": 211648,
        "sellerId": 1347797,
        "paymentType": "BAL",
    }
    return base | fields


def source(lines: list[dict[str, Any]]) -> WildberriesSource:
    return WildberriesSource(FakeSession(lines), images=NoImages(), offices=KnownOffices())


def test_it_is_a_marketplace_source() -> None:
    assert isinstance(source([]), MarketplaceSource)


def test_a_returned_item_is_not_owned() -> None:
    """«Refund» arrived and went back. An inventory that counts it is wrong."""
    parcels = source([line(status="Refund")]).parcels("order-1")
    assert parcels[0].items[0].received is False


def test_a_kept_item_is_owned() -> None:
    parcels = source([line(status="Purchased")]).parcels("order-1")
    assert parcels[0].items[0].received is True


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["Purchased", "Refund"], OrderState.RECEIVED),
        (["Rejected", "FailedPayment"], OrderState.CANCELLED),
        (["Purchased", "Что-то новое"], OrderState.ACTIVE),
    ],
)
def test_an_order_is_settled_only_when_none_of_it_is_moving(statuses: list[str], expected: OrderState) -> None:
    """A word WB has not used before must not read as settled: an unknown state
    is one that might still change, and the sync re-reads those.
    """
    orders = list(source([line(status=s) for s in statuses]).orders(10))
    assert orders[0].state is expected


def test_prices_are_rendered_from_the_kopecks_wb_states() -> None:
    parcels = source([line(rawPrice=161200)]).parcels("order-1")
    assert parcels[0].items[0].price == "1 612 ₽"
    assert parcels[0].paid_total == "1 612 ₽"


def test_kopecks_are_not_dropped() -> None:
    parcels = source([line(rawPrice=161234)]).parcels("order-1")
    assert parcels[0].items[0].price == "1 612,34 ₽"


def test_a_parcel_is_what_went_to_one_place() -> None:
    """WB names no parcel, so one order to two places is two parcels — and the
    address is what tells them apart.
    """
    lines = [line(officeId=211648), line(code1S=559802752, officeId=50134318)]
    parcels = sorted(source(lines).parcels("order-1"), key=lambda p: p.shipment_id or "")
    assert len(parcels) == 2
    addresses = {p.delivery_address for p in parcels}
    assert "Санкт-Петербург, Белорусская Улица 4" in addresses
    assert None in addresses  # a warehouse is not a pickup point
    assert {p.delivery_kind for p in parcels} == {"Доставка в пункт выдачи", "Доставка курьером"}


def test_the_total_is_the_parcel_not_the_order() -> None:
    lines = [line(rawPrice=100_00, officeId=211648), line(code1S=2, rawPrice=200_00, officeId=50134318)]
    by_office = {p.delivery_kind: p.paid_total for p in source(lines).parcels("order-1")}
    assert by_office["Доставка в пункт выдачи"] == "100 ₽"
    assert by_office["Доставка курьером"] == "200 ₽"


def test_the_archive_is_fetched_once_however_often_it_is_read() -> None:
    """All three reads come out of one answer; a sync calls them in a row."""
    session = FakeSession([line()])
    wb = WildberriesSource(session, images=NoImages(), offices=KnownOffices())
    wb.purchases(10)
    list(wb.orders(10))
    wb.parcels("order-1")
    assert session.calls == 1


def test_one_product_bought_twice_is_one_product() -> None:
    wb = source([line(), line(orderId="order-2")])
    assert len(wb.purchases(10)) == 1


def test_brand_and_name_are_one_title() -> None:
    assert source([line()]).purchases(10)[0].title == "ОБОЮДНО Shorts"


def test_size_and_colour_are_the_variant() -> None:
    assert source([line()]).parcels("order-1")[0].items[0].variant == "M・blue"
