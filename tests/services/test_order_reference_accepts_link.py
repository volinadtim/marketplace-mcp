"""list_orders hands out a link; the order tools have to take it.

Only the products tool ever accepted one, so cancelling or paying an order the
caller had just listed failed on the identifier it was given.
"""

from __future__ import annotations

import base64
import json

import pytest

from marketplace_mcp.adapters.ozon.parsing.orders import order_numbers_from_link
from marketplace_mcp.adapters.ozon.services.orders import resolve_order
from marketplace_mcp.core.errors import OzonError


def _posting_link(*postings: str) -> str:
    blob = base64.urlsafe_b64encode(json.dumps({"postings": list(postings)}).encode()).decode()
    return f"v2/cacheOrderProducts?data={blob}"


def test_a_number_passes_through() -> None:
    assert resolve_order("44563249-0877") == "44563249-0877"
    assert resolve_order("  44563249-0877 ") == "44563249-0877"


def test_a_detail_link_is_decoded_to_its_order_number() -> None:
    # The link lists parcels ("…-0877-1"); the order is the number without them.
    detail_link = _posting_link("44563249-0877-1", "44563249-0877-2")
    assert order_numbers_from_link(detail_link) == ["44563249-0877"]
    assert resolve_order(detail_link) == "44563249-0877"


def test_a_split_order_resolves_to_its_first_number() -> None:
    assert resolve_order(_posting_link("44563249-0877-1", "44563249-0901-1")) == "44563249-0877"


def test_something_that_is_neither_says_what_to_pass() -> None:
    with pytest.raises(OzonError) as raised:
        resolve_order("вчерашний заказ")
    assert "list_orders()" in str(raised.value)
