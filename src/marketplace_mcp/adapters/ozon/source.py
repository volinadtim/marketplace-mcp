"""Ozon, in the terms the core keeps.

Everything Ozon-shaped stops here. The services answer in DTOs that carry
Ozon's own wording and its own idea of what an order is; this turns them into
the plain records the store speaks, and nothing on the far side of it needs to
know the word "composer-api" again.
"""

from collections.abc import Iterable
from typing import Final

from marketplace_mcp.adapters.ozon.models.enums import OrderState as OzonOrderState
from marketplace_mcp.adapters.ozon.models.orders import Order, OrderDetail
from marketplace_mcp.adapters.ozon.services import catalog, orders as orders_service
from marketplace_mcp.core.records import (
    OrderRecord,
    OrderState,
    ParcelItemRecord,
    ParcelRecord,
    ProductRecord,
)

_STATES: Final = {
    OzonOrderState.ACTIVE: OrderState.ACTIVE,
    OzonOrderState.RECEIVED: OrderState.RECEIVED,
    OzonOrderState.CANCELLED: OrderState.CANCELLED,
}


class OzonSource:
    """The three reads the store needs, over the ozon.ru buyer account."""

    name = "ozon"

    def purchases(self, limit: int) -> list[ProductRecord]:
        """«Купленные товары» — everything ever ordered, with today's price.

        Ozon's list is a list of products, not of outcomes: it holds what was
        refused at the pickup point too, and states no status at all. What
        became of any of it comes from the orders.
        """
        return [
            ProductRecord(
                sku=tile.sku,
                title=tile.title,
                image=tile.image,
                url=tile.url,
                seller=tile.seller,
                price=tile.price,
            )
            for tile in catalog.purchases(limit=limit)
            if tile.sku
        ]

    def orders(self, limit: int) -> Iterable[OrderRecord]:
        """Orders newest first, the archive included.

        Yields:
            Each order as the list states it, newest first, so a caller can
            stop as soon as it reaches one it already has.

        """
        for order in orders_service.list_orders("all", limit):
            if order.order_number:
                yield _order(order)

    def parcels(self, order_number: str) -> list[ParcelRecord]:
        """Each parcel of an order, with where it went and what it held."""
        return [_parcel(detail) for detail in orders_service.order_parcels(order_number)]


def _order(order: Order) -> OrderRecord:
    return OrderRecord(
        number=order.order_number or "",
        state=_STATES.get(order.state, OrderState.ACTIVE),
        status=order.status,
        status_date=order.date,
    )


def _parcel(detail: OrderDetail) -> ParcelRecord:
    delivery = detail.delivery
    return ParcelRecord(
        order_number=detail.order_number or "",
        shipment_id=detail.shipment_id,
        status=detail.status,
        delivery_kind=delivery.kind if delivery else None,
        delivery_address=delivery.address if delivery else None,
        recipient=detail.recipient,
        paid_total=detail.paid_total,
        payment_method=detail.payment_method,
        items=[
            ParcelItemRecord(
                sku=product.sku,
                title=product.title,
                variant=product.variant,
                seller=product.seller,
                price=product.price,
                received=product.received,
            )
            for product in detail.products
            if product.sku
        ],
    )
