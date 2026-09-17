"""Wildberries, in the terms the core keeps.

One endpoint carries almost everything. WB's order archive answers with a row
per item — the product, its size and colour, what was paid for it, when it was
ordered, what became of it, and which pickup point it went to — so the three
reads the core asks for are three views of one fetch rather than three walks.

That is the shape of the site, not a shortcut: WB has no notion of a parcel in
this API, so a parcel here is what one order sent to one place amounts to.
"""

import logging
from collections.abc import Iterable
from typing import Any, Final

from marketplace_mcp.adapters.wildberries.constants import (
    ARCHIVE,
    HOME,
    RETURNED,
    STATES,
)
from marketplace_mcp.adapters.wildberries.images import ImageFinder
from marketplace_mcp.adapters.wildberries.offices import PickupPoints
from marketplace_mcp.adapters.wildberries.session import WildberriesSession
from marketplace_mcp.core.records import (
    OrderRecord,
    OrderState,
    ParcelItemRecord,
    ParcelRecord,
    ProductRecord,
)

logger = logging.getLogger(__name__)

_KOPECKS: Final = 100


def _money(kopecks: Any) -> str | None:
    """The rendered form of an amount WB states only as a number.

    Every other marketplace hands over a string it rendered itself and the
    store keeps it as-is; WB hands over kopecks, so the rendering happens here
    rather than leaving the field empty or the store guessing a currency.
    """
    if not isinstance(kopecks, int):
        return None
    whole, cents = divmod(kopecks, _KOPECKS)
    grouped = f"{whole:,}".replace(",", " ")
    return f"{grouped} ₽" if not cents else f"{grouped},{cents:02d} ₽"


def _title(line: dict[str, Any]) -> str | None:
    """Brand and name together, the way the site shows them."""
    brand, name = (line.get("brand") or "").strip(), (line.get("name") or "").strip()
    return " ".join(part for part in (brand, name) if part) or None


def _variant(line: dict[str, Any]) -> str | None:
    """Size and colour, joined as one line so it reads like Ozon's."""
    parts = [str(line.get(key) or "").strip() for key in ("size", "color")]
    return "・".join(part for part in parts if part) or None


class WildberriesSource:
    """The three reads the store needs, over a wildberries.ru account."""

    name = "wildberries"

    def __init__(
        self,
        session: WildberriesSession,
        images: ImageFinder | None = None,
        offices: PickupPoints | None = None,
    ) -> None:
        self._session = session
        self._images = images if images is not None else ImageFinder()
        self._offices = offices if offices is not None else PickupPoints()
        self._archive: list[dict[str, Any]] | None = None

    # -- the one fetch -------------------------------------------------------

    def archive(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Every order line the account has, newest first.

        Held for the life of the source because all three reads come out of it
        and a sync calls them one after another; a second fetch would ask WB the
        same question twice for one answer.
        """
        if self._archive is None or refresh:
            answer = self._session.post(ARCHIVE, {})
            lines = ((answer or {}).get("value") or {}).get("archive") or []
            self._archive = sorted(lines, key=lambda line: str(line.get("orderDate") or ""), reverse=True)
            logger.info("Wildberries archive holds %d lines", len(self._archive))
        return self._archive

    # -- MarketplaceSource ---------------------------------------------------

    def purchases(self, limit: int) -> list[ProductRecord]:
        """The products bought, one row each, with a picture found for it.

        The price is what was paid, not what the product costs today — WB's
        history states no current price at all. The store reads it as a price
        observation all the same, which makes the series for a WB product a
        record of what its purchases cost rather than of what it was listed at.
        """
        products: dict[str, ProductRecord] = {}
        for line in self.archive()[:limit]:
            sku = str(line.get("code1S") or "")
            if not sku or sku in products:
                continue
            products[sku] = ProductRecord(
                sku=sku,
                title=_title(line),
                image=self._images.find(int(sku)) if sku.isdigit() else None,
                url=f"{HOME}/catalog/{sku}/detail.aspx",
                seller=str(line["sellerId"]) if line.get("sellerId") else None,
                price=_money(line.get("rawPrice")),
            )
        return list(products.values())

    def orders(self, limit: int) -> Iterable[OrderRecord]:
        """Orders newest first, each folded from the lines that belong to it.

        An order's state is the state of its lines, and they can disagree: one
        item kept and another sent back is one order. It counts as settled when
        nothing in it is still moving, which is what the store needs the state
        for.

        Yields:
            One record per order, newest first, so a sync can stop as soon as
            it reaches one it already holds.

        """
        seen: dict[str, list[dict[str, Any]]] = {}
        for line in self.archive():
            number = str(line.get("orderId") or "")
            if number:
                seen.setdefault(number, []).append(line)

        for number, lines in list(seen.items())[:limit]:
            states = {STATES.get(str(line.get("status")), OrderState.ACTIVE) for line in lines}
            state = OrderState.ACTIVE if OrderState.ACTIVE in states else next(iter(states))
            last = max((str(line.get("lastDate") or "") for line in lines), default="")
            yield OrderRecord(
                number=number,
                state=state,
                status="; ".join(sorted({str(line.get("status")) for line in lines})),
                status_date=(last or str(lines[0].get("orderDate") or ""))[:10] or None,
            )

    def parcels(self, order_number: str) -> list[ParcelRecord]:
        """What that order sent, grouped by where it was sent.

        Costs no request: the archive already holds it. WB names no parcel, so
        one is taken to be an order's lines that went to the same place — which
        is what a parcel is, and the only grouping the data supports.
        """
        lines = [line for line in self.archive() if str(line.get("orderId") or "") == order_number]
        by_office: dict[int, list[dict[str, Any]]] = {}
        for line in lines:
            by_office.setdefault(int(line.get("officeId") or 0), []).append(line)

        parcels: list[ParcelRecord] = []
        for office_id, group in by_office.items():
            total = sum(int(line.get("rawPrice") or 0) for line in group)
            parcels.append(
                ParcelRecord(
                    order_number=order_number,
                    shipment_id=f"{order_number}:{office_id}",
                    status="; ".join(sorted({str(line.get("status")) for line in group})),
                    delivery_kind=self._offices.kind(office_id),
                    delivery_address=self._offices.address(office_id),
                    recipient=None,
                    paid_total=_money(total),
                    payment_method=next((str(line["paymentType"]) for line in group if line.get("paymentType")), None),
                    items=[
                        ParcelItemRecord(
                            sku=str(line.get("code1S") or ""),
                            title=_title(line),
                            variant=_variant(line),
                            seller=str(line["sellerId"]) if line.get("sellerId") else None,
                            price=_money(line.get("rawPrice")),
                            received=_received(str(line.get("status"))),
                        )
                        for line in group
                        if line.get("code1S")
                    ],
                )
            )
        return parcels


def _received(status: str) -> bool | None:
    """Whether the item reached the buyer and stayed with them.

    «Refund» is deliberately False: the thing did arrive, but it is not owned
    now, and an inventory that counts it is wrong in the way that matters.
    """
    if status in RETURNED:
        return False
    state = STATES.get(status)
    if state is None:
        return None
    return state == OrderState.RECEIVED
