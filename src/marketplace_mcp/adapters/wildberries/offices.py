"""Turning the number of a pickup point into the place it is.

An order states only ``officeId``. The addresses live in a directory WB serves
to anyone, unauthenticated, as one 7 MB file — so it is fetched once and kept
for the life of the process rather than asked per order.

Not every id is in it. Ids from fifty million up are warehouses: those orders
were carried to a door, no pickup point took part, and there is nothing to
resolve. Two of this account's eight were of that kind.
"""

import logging
from typing import Any, Final

from curl_cffi import requests

from marketplace_mcp.adapters.wildberries.constants import OFFICES_DIRECTORY, WAREHOUSE_ID_FROM

logger = logging.getLogger(__name__)

_TIMEOUT: Final = 90.0


class PickupPoints:
    """The public directory, fetched on first use."""

    def __init__(self, session: Any = None) -> None:
        self._http = session or requests.Session(impersonate="chrome124")
        self._addresses: dict[int, str] | None = None

    def _load(self) -> dict[int, str]:
        """Read the directory into ids and addresses.

        A directory that cannot be fetched is not fatal: an order without an
        address is still an order, and the alternative is refusing to sync at
        all because a CDN was slow.
        """
        try:
            response = self._http.get(OFFICES_DIRECTORY, timeout=_TIMEOUT)
            response.raise_for_status()
            groups = response.json()
        except Exception as unreachable:  # ruff: ignore[blind-except]
            logger.warning("could not fetch the pickup directory: %s", unreachable)
            return {}

        addresses: dict[int, str] = {}
        for group in groups if isinstance(groups, list) else []:
            for office in (group or {}).get("items") or []:
                if isinstance(office, dict) and "id" in office and office.get("address"):
                    addresses[int(office["id"])] = str(office["address"])
        logger.info("pickup directory holds %d points", len(addresses))
        return addresses

    def address(self, office_id: int | None) -> str | None:
        """Where that pickup point is, or None if it is not one."""
        if not office_id or office_id >= WAREHOUSE_ID_FROM:
            return None
        if self._addresses is None:
            self._addresses = self._load()
        return self._addresses.get(int(office_id))

    @staticmethod
    def kind(office_id: int | None) -> str | None:
        """How the order reached its buyer, in as much as the id says.

        WB does not state this in words the way Ozon does, so it is inferred
        from the one thing it does state: a warehouse id means the parcel was
        carried, a pickup id means it was collected.
        """
        if not office_id:
            return None
        return "Доставка курьером" if office_id >= WAREHOUSE_ID_FROM else "Доставка в пункт выдачи"
