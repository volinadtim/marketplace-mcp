"""What the store is told, whichever marketplace is telling it.

An adapter reads its own site and answers in its own shapes — Ozon's carry the
strings Ozon renders, wording and all. These are what is left once that is
stripped away: the facts every marketplace states about a thing bought, in the
same fields, so the store does not have to know which site they came from.

Money stays as the site rendered it. The number is parsed on the way into the
store, where it can be compared, but the rendered form is kept beside it: it is
what a person recognises on a receipt, and no two marketplaces render alike.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class OrderState(StrEnum):
    """Where an order has got to, in the only three states worth storing.

    Every marketplace has its own vocabulary for this and far more states than
    three; what a local copy needs to know is whether the outcome can still
    change. ``ACTIVE`` means it can, and so the order is re-read on every sync.
    """

    ACTIVE = "active"
    """Still moving: on its way, awaiting collection, awaiting payment."""

    RECEIVED = "received"
    """It arrived. Nothing about it will change again."""

    CANCELLED = "cancelled"
    """It will not arrive. Also final."""


@dataclass(frozen=True, slots=True)
class ProductRecord:
    """A product as its marketplace's own list of purchases shows it.

    ``price`` is what the site asks for it *today*, not what was paid — those
    are different numbers, and the second one lives on the order. Stored as a
    reading with a time on it, which is the only way a price history exists at
    all: marketplaces show one price, the current one.
    """

    sku: str
    title: str | None = None
    image: str | None = None
    url: str | None = None
    seller: str | None = None
    price: str | None = None


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """An order as its list shows it: what became of it, and when."""

    number: str
    state: OrderState = OrderState.ACTIVE
    status: str | None = None
    """The marketplace's own words — «Получен 14 сентября», «Отменён»."""

    status_date: str | None = None
    """ISO date that status refers to, where the site states one."""


@dataclass(frozen=True, slots=True)
class ParcelItemRecord:
    """One item as it travelled, with what was actually paid for it."""

    sku: str
    title: str | None = None
    variant: str | None = None
    seller: str | None = None
    price: str | None = None
    received: bool | None = None
    """True if it arrived, False if its parcel was cancelled, None if unsaid.

    None is not False. A marketplace often states no outcome per item even on a
    delivered order, and storing that as "not received" invents a refusal.
    """


@dataclass(frozen=True, slots=True)
class ParcelRecord:
    """A parcel of an order: where it went, and what was in it.

    The parcel is the unit, not the order, because that is the unit the facts
    attach to. One order can split into parcels that go to different places and
    meet different ends — an item refused at a pickup point cancels its parcel
    while the rest of the order arrives.

    ``paid_total`` and ``payment_method`` describe the whole order and repeat on
    each of its parcels; they are carried here because this is where the sites
    state them.
    """

    order_number: str
    shipment_id: str | None = None
    status: str | None = None
    delivery_kind: str | None = None
    """The site's own line: «Доставка в пункт выдачи», «Доставка курьером»."""

    delivery_address: str | None = None
    recipient: str | None = None
    paid_total: str | None = None
    payment_method: str | None = None
    items: list[ParcelItemRecord] = field(default_factory=list)
