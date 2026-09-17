"""What the core needs a marketplace to be able to do.

Three reads, because three are enough to keep a local copy: what has been
bought, what the orders became, and where each parcel went. Everything a site
offers beyond that — a cart, a catalogue, a checkout — belongs to its adapter
and is none of the store's business.

A Protocol rather than a base class: an adapter is already a package of plain
functions against its own site, and should not have to inherit from the core to
be usable by it.
"""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from marketplace_mcp.core.records import OrderRecord, ParcelRecord, ProductRecord


@runtime_checkable
class MarketplaceSource(Protocol):
    """One marketplace, read-only, in the terms the store keeps."""

    name: str
    """Short identifier stored beside every row — "ozon", "wildberries".

    Kept on the rows because a sku only identifies a product within the site
    that issued it, and two marketplaces will hand out the same digits sooner
    or later.
    """

    def purchases(self, limit: int) -> list[ProductRecord]:
        """Everything ever bought, as the site's own list of it.

        Cheap enough to walk whole: it is one paginated list, and it is where
        the picture and the current price come from.
        """
        ...

    def orders(self, limit: int) -> Iterable[OrderRecord]:
        """Orders, newest first.

        Newest first is part of the contract, not an accident of the site: it
        is what lets a sync stop as soon as it reaches an order it already has
        and knows to be settled.
        """
        ...

    def parcels(self, order_number: str) -> list[ParcelRecord]:
        """The parcels of one order, with their addresses and their items.

        Expected to cost a request or more per order — this is the expensive
        read, and the reason a sync tries hard not to repeat it.
        """
        ...
