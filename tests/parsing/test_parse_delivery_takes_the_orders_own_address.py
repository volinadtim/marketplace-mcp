"""The delivery address is the order's, not the one in the site header.

Every order page carries the header's address picker — whichever address is
selected for new orders — and it is the same on all of them. Reading that
instead gives a whole purchase history one address, and it looks right.
"""

from __future__ import annotations

from ozon_mcp.parsing.orders import parse_delivery, parse_order_detail
from support import page

PICKUP = "Пункт Ozon, Россия, Санкт-Петербург, Малый проспект Васильевского острова, 17"
HEADER = "7-я лн. В.О., 72"


def _address_widget(kind: str, address: str) -> dict[str, object]:
    return {
        "cell": {
            "title": {"text": kind, "testInfo": {"automatizationId": "addressDetails"}},
            "subtitle": {"text": address},
        },
        "testInfo": {"automatizationId": "details-address"},
    }


def _recipient_widget() -> dict[str, object]:
    return {
        "cell": {"title": {"text": "Получатель"}, "subtitle": {"text": "Кто-то, +7 000 000 00 00"}},
        "testInfo": {"automatizationId": "details-recipient"},
    }


def test_the_tagged_block_wins_over_the_header() -> None:
    data = page(
        addressBookBarWeb={"cells": [{"title": {"text": "Пункт Ozon •"}, "subtitle": {"text": HEADER}}]},
        orderDetailsItem=_address_widget("Доставка в пункт выдачи", PICKUP),
    )
    delivery = parse_delivery(data)
    assert delivery is not None
    assert delivery.address == PICKUP
    assert delivery.kind == "Доставка в пункт выдачи"


def test_the_recipient_block_is_not_an_address() -> None:
    assert parse_delivery(page(orderDetailsItem=_recipient_widget())) is None


def test_a_page_without_a_parcel_states_no_address() -> None:
    assert parse_delivery(page(orderDoneTotal={})) is None


def test_the_total_belongs_to_the_order_not_the_parcel() -> None:
    data = page(
        shipmentWidget={
            "shipmentId": 43607436935,
            "header": [
                {"textIcon": {"text": {"text": "Получен", "testInfo": {"automatizationId": "shipment-status"}}}}
            ],
        },
        orderDoneTotal={
            "total": {
                "left": {
                    "title": {"text": "Оплачено", "testInfo": {"automatizationId": "total-title"}},
                    "subtitle": {"text": "Ozon Банк", "testInfo": {"automatizationId": "total-subtitle"}},
                },
                "right": {"price": {"text": "1 958,42 ₽", "testInfo": {"automatizationId": "total-price"}}},
            }
        },
        orderDetailsItem=_address_widget("Доставка курьером", "Россия, Санкт-Петербург, 6-я линия В.О., 43"),
    )
    detail = parse_order_detail(data, order_number="64060183-0603")
    assert detail.shipment_id == "43607436935"
    assert detail.status == "Получен"
    assert detail.paid_total == "1 958,42 ₽"
    assert detail.payment_method == "Ozon Банк"
    assert detail.delivery is not None
    assert detail.delivery.kind == "Доставка курьером"


def _parcel(shipment: str, status: str, sku: str, title: str) -> dict[str, object]:
    return {
        "shipmentId": shipment,
        "header": [{"textIcon": {"text": {"text": status, "testInfo": {"automatizationId": "shipment-status"}}}}],
        "items": [
            {
                "sellers": [
                    {
                        "name": {"text": "Продавец"},
                        "products": [
                            {
                                "title": {
                                    "name": {"text": title},
                                    "common": {"action": {"link": f"/product/x-{sku}/", "id": sku}},
                                },
                                "price": {"price": [{"text": "219 ₽"}]},
                            }
                        ],
                    }
                ]
            }
        ],
    }


def _two_parcel_page() -> dict[str, object]:
    data = page(orderDetailsItem=_address_widget("Доставка в пункт выдачи", PICKUP))
    states = data["widgetStates"]
    from ozon_mcp.utils.serde import dumps

    states["shipmentWidget-1-default-1"] = dumps(_parcel("111", "Получен", "1001", "Первый"))
    states["shipmentWidget-1-default-1-2"] = dumps(_parcel("222", "Отменён", "2002", "Второй"))
    return data


def test_a_parcel_answers_for_itself_not_for_the_order() -> None:
    second = parse_order_detail(_two_parcel_page(), order_number="64060183-0603", shipment="222")
    assert second.shipment_id == "222"
    assert second.status == "Отменён"
    assert [product.sku for product in second.products] == ["2002"]


def test_without_a_parcel_named_the_first_one_answers() -> None:
    first = parse_order_detail(_two_parcel_page(), order_number="64060183-0603")
    assert first.shipment_id == "111"
    assert [product.sku for product in first.products] == ["1001"]
