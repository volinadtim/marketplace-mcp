"""Parse order widgets into DTOs, including archive dates."""

import base64
import datetime
import re
from typing import Any, Final

from marketplace_mcp.adapters.ozon.models.enums import OrderState
from marketplace_mcp.adapters.ozon.models.orders import Delivery, Order, OrderDetail, OrderLine, OrderProduct
from marketplace_mcp.adapters.ozon.parsing.common import find_all, walk, widget, widgets_all
from marketplace_mcp.core.utils.serde import loads

_RU_MONTHS: Final = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def order_ids_from_link(link: str | None) -> list[int]:
    """Decode orderIds from an order's cacheOrderProducts ``data=`` blob (used as
    the archiveOrdersStart cursor for the completed-orders history).
    """
    match = re.search(r"data=([A-Za-z0-9_\-=]+)", link or "")
    if not match:
        return []
    try:
        token = match.group(1) + "=" * (-len(match.group(1)) % 4)
        decoded = loads(base64.urlsafe_b64decode(token))
        return [int(x) for x in decoded.get("orderIds", [])]
    except (ValueError, TypeError):
        return []


def order_numbers_from_link(link: str | None) -> list[str]:
    """Order numbers behind an order row.

    The row's ``detail_link`` is a cacheOrderProducts blob listing *postings*
    ("44563249-0833-6"); the order page is keyed by the number without the
    trailing parcel segment.
    """
    match = re.search(r"data=([A-Za-z0-9_\-=]+)", link or "")
    if not match:
        return []
    try:
        token = match.group(1) + "=" * (-len(match.group(1)) % 4)
        postings = loads(base64.urlsafe_b64decode(token)).get("postings", [])
    except (ValueError, TypeError):
        return []
    numbers: list[str] = []
    for posting in postings:
        number = "-".join(str(posting).split("-")[:2])
        if number and number not in numbers:
            numbers.append(number)
    return numbers


def parse_ru_date(text: object, *, upcoming: bool = False) -> str | None:
    """Parse «Получен 24 августа [2025]» → ISO ``YYYY-MM-DD``.

    Ozon omits the year on near dates, so it is inferred: a status looks back, a
    deadline («Хранится до 14 сентября») looks forward — ``upcoming`` picks which.
    """
    if not isinstance(text, str):
        return None
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\s*(\d{4})?", text.replace(" ", " "), re.IGNORECASE)
    if not match:
        return None
    month = next((v for k, v in _RU_MONTHS.items() if match.group(2).lower().startswith(k)), None)
    if not month:
        return None
    today = datetime.date.today()
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        parsed = datetime.date(year, month, int(match.group(1)))
    except ValueError:
        return None
    if not match.group(3):
        if upcoming and parsed < today:
            parsed = parsed.replace(year=year + 1)
        elif not upcoming and parsed > today:
            parsed = parsed.replace(year=year - 1)
    return parsed.isoformat()


# An order number as Ozon writes it: "44563249-0877".
ORDER_NUMBER_RE: Final = re.compile(r"\d{6,}-\d{3,}")
_ORDER_PARAM_RE: Final = re.compile(r"[?&]order=(\d{6,}-\d{3,})")


def order_numbers_in(row: Any) -> list[str]:
    """Order numbers a listed row is about.

    A row is a delivery group, not an order: its own link bundles the postings of
    every order arriving together, so decoding that link gives the whole bundle
    and pins nothing to this row. Each product on the row, though, links to its
    own order — ``/my/orderdetails/?order=44563249-0833`` — which is where the
    number for *this* row is.
    """
    found: list[str] = []
    for node in walk(row):
        link = node.get("link")
        match = _ORDER_PARAM_RE.search(link) if isinstance(link, str) else None
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


# The row renders the item price, the item's payment badge and the group's
# pay-on-pickup sum as the same price atom, so the automatizationId Ozon tags
# each with is the only way to tell them apart.
_STATUS_TAG: Final = "tileStatus"
_POSTPAY_CELL_TAG: Final = "postpay_sum_cell"
_ITEM_PAYMENT_TAG: Final = "itemPay"
_ITEM_PRICE_TAG: Final = "payMoney"
_ITEM_QUANTITY_TAG: Final = "itemQuantity"
_ITEM_STORAGE_TAG: Final = "postingPvzExpiration"
_PAID_BADGE: Final = "Оплачен"
# Ozon's status vocabulary; anything else («В пути», «Можно забирать») is active.
_TERMINAL_STATUSES: Final = {"Получен": OrderState.RECEIVED, "Отменён": OrderState.CANCELLED}


def _atom(value: Any) -> str | None:
    """Text of a value that may be a plain string or a ``{"text": …}`` atom."""
    if isinstance(value, str):
        return str(value).strip() or None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return str(text).strip() or None
    return None


def _tagged(node: Any, tag: str) -> dict[str, Any] | None:
    """The node Ozon tagged with this ``automatizationId``, which hangs either on
    the node itself or under its ``common``.
    """
    for candidate in walk(node):
        for holder in (candidate, candidate.get("common")):
            info = holder.get("testInfo") if isinstance(holder, dict) else None
            if isinstance(info, dict) and info.get("automatizationId") == tag:
                return candidate
    return None


def _tagged_text(node: Any, tag: str) -> str | None:
    return _atom(_tagged(node, tag))


def _money(node: Any) -> str | None:
    """The sum in a price node, rendered as a list of styled parts — either the
    price node itself (``price`` is the list) or a block holding one (``price.price``).
    """
    parts = node.get("price") if isinstance(node, dict) else None
    if isinstance(parts, dict):
        parts = parts.get("price")
    if not isinstance(parts, list):
        return None
    return next((_atom(part) for part in parts if _atom(part)), None)


def _state(status: str | None) -> OrderState:
    first = (status or "").split()
    return _TERMINAL_STATUSES.get(first[0], OrderState.ACTIVE) if first else OrderState.ACTIVE


def _line(product: dict[str, Any]) -> OrderLine:
    media = (product.get("image") or {}).get("productMedia") or {}
    link = ((media.get("common") or {}).get("action") or {}).get("link")
    badge = _tagged_text(product, _ITEM_PAYMENT_TAG)
    number = _ORDER_PARAM_RE.search(link) if isinstance(link, str) else None
    return OrderLine(
        photo=(media.get("image") or {}).get("url"),
        detail_link=link,
        order_number=number.group(1) if number else None,
        paid=badge.startswith(_PAID_BADGE) if badge else None,
        payment_status=badge,
        price=_money(_tagged(product, _ITEM_PRICE_TAG)),
        quantity=_tagged_text(product, _ITEM_QUANTITY_TAG),
        stored_until=parse_ru_date(_tagged_text(product, _ITEM_STORAGE_TAG), upcoming=True),
    )


def parse_orders(data: dict[str, Any]) -> list[Order]:
    """Orders from the ordersV2 widget (active list or archive).

    Money comes from the tagged nodes: the group's «К оплате при получении» from
    one cell of the row's cellList, the price and payment badge from each item.
    The row states no order total.
    """
    state = widget(data, "orderList")
    rows = state.get("ordersV2") if isinstance(state, dict) else None
    orders: list[Order] = []
    for row in rows or []:
        left = row.get("leftBlock") or {}
        products = ((row.get("rightBlock") or {}).get("products") or {}).get("products") or []
        action = (row.get("common") or {}).get("action") or {}
        numbers = order_numbers_in(row)
        status = _tagged_text(left.get("textIcon") or {}, _STATUS_TAG) or _atom(
            next(iter(find_all(left.get("textIcon") or {}, "text")), None)
        )
        slot = _atom(left.get("subtitle"))
        postpay = _tagged(left, _POSTPAY_CELL_TAG)
        orders.append(
            Order(
                pickup=_atom(left.get("title")),
                slot=slot,
                delivery_eta=slot,
                status=status,
                state=_state(status),
                date=next((parse_ru_date(text) for text in (status, slot) if parse_ru_date(text)), None),
                amount_due_at_pickup=_money((postpay or {}).get("rightBlock")),
                items_count=len(products),
                products=[_line(product) for product in products if isinstance(product, dict)],
                detail_link=action.get("link"),
                order_ids=order_ids_from_link(action.get("link")),
                order_number=next(iter(numbers), None),
                order_numbers=numbers,
            )
        )
    return orders


_PRODUCT_LINK_RE: Final = re.compile(r"/product/(?:[a-z0-9\-]+-)?(\d{6,})")


# Each parcel states its own outcome in its header, and Ozon tags it.
_SHIPMENT_STATUS_TAG: Final = "shipment-status"
_RECEIVED_STATUS: Final = "Получен"
_CANCELLED_STATUS: Final = "Отменён"


def parse_order_products(data: dict[str, Any], order_number: str | None = None) -> list[OrderProduct]:
    """Products of an order-details page, read from their own fields.

    The page nests them as ``shipmentWidget.items[].sellers[].products[]`` — one
    shipmentWidget per parcel, grouped by seller — and each product carries its
    name, price, chosen variant and a link whose action id *is* the sku. Reading
    that structure avoids guessing: the page also renders statuses, tracking
    sentences and seller names that no text-shape heuristic can tell from a
    product name.

    Each parcel also states its own outcome, and one order holds parcels with
    different ones: refusing an item at the pickup point cancels its parcel while
    the rest of the order is received. Flattening the parcels — which is what
    this did — lost that, and «Купленные товары» plus an order number then read
    as "bought" for something nobody took home. So the status travels with the
    item, and a sku appearing in two parcels is kept once per parcel.
    """
    products: list[OrderProduct] = []
    seen: set[tuple[str, str]] = set()
    for state in widgets_all(data, "shipmentWidget"):
        shipment_id = str(state.get("shipmentId") or "") or None
        status = _tagged_text(state.get("header") or [], _SHIPMENT_STATUS_TAG)
        for item in state.get("items") or []:
            if not isinstance(item, dict):
                continue
            for seller in item.get("sellers") or []:
                if not isinstance(seller, dict):
                    continue
                seller_name = _atom(seller.get("name"))
                for product in seller.get("products") or []:
                    if not isinstance(product, dict):
                        continue
                    title = product.get("title") or {}
                    action = ((title.get("common") or {}).get("action")) or {}
                    sku = str(action.get("id") or "")
                    if not sku:
                        match = _PRODUCT_LINK_RE.search(str(action.get("link") or ""))
                        sku = match.group(1) if match else ""
                    if not sku or (sku, shipment_id or "") in seen:
                        continue
                    seen.add((sku, shipment_id or ""))
                    price_texts = (product.get("price") or {}).get("price") or []
                    attributes = product.get("attributes") or []
                    products.append(
                        OrderProduct(
                            sku=sku,
                            title=_atom(title.get("name")),
                            price=next((_atom(entry) for entry in price_texts if _atom(entry)), None),
                            variant=next((_atom(entry) for entry in attributes if _atom(entry)), None),
                            seller=seller_name,
                            url=f"https://www.ozon.ru/product/{sku}/",
                            order_number=order_number,
                            shipment_id=shipment_id,
                            shipment_status=status,
                            received=_received(status),
                        )
                    )
    return products


def _received(status: str | None) -> bool | None:
    """Whether the parcel reached the buyer, from Ozon's own word for it."""
    if not status:
        return None
    if status.startswith(_RECEIVED_STATUS):
        return True
    return False if status.startswith(_CANCELLED_STATUS) else None


# The order page tags its delivery block, and the line inside it that names the
# kind of delivery; the address is the sentence under that line.
_ADDRESS_WIDGET_TAG: Final = "details-address"
_ADDRESS_KIND_TAG: Final = "addressDetails"
_RECIPIENT_WIDGET_TAG: Final = "details-recipient"
_TOTAL_PRICE_TAG: Final = "total-price"
_TOTAL_METHOD_TAG: Final = "total-subtitle"


def parse_delivery(data: dict[str, Any]) -> Delivery | None:
    """Where this parcel went, from the block the page tags as its address.

    The page carries two addresses and only one of them belongs to the order.
    The other is the site header's address picker — whichever address is
    currently selected for *new* orders — and it is byte-identical on every
    order page, so a search for something address-shaped answers with it and
    every order in a history comes back delivered to the same place. Selecting
    the tagged block is what tells them apart.

    A parcel states an address only when a parcel is named: the order page on
    its own describes an order that may have gone to four different places.
    """
    cell = _detail_cell(data, _ADDRESS_WIDGET_TAG)
    if cell is None:
        return None
    address = _atom(cell.get("subtitle"))
    if not address:
        return None
    return Delivery(kind=_tagged_text(cell, _ADDRESS_KIND_TAG) or _atom(cell.get("title")), address=address)


def _detail_cell(data: dict[str, Any], tag: str) -> dict[str, Any] | None:
    """The cell of the order-detail block Ozon tagged with ``tag``.

    The blocks are separate widgets of one name and their order is not stable —
    the same order serves address-then-recipient on one parcel and the reverse
    on the next — so they are told apart by their tag and never by position.
    """
    for state in widgets_all(data, "orderDetailsItem"):
        if not isinstance(state, dict) or _tagged(state, tag) is None:
            continue
        cell = state.get("cell")
        if isinstance(cell, dict):
            return cell
    return None


def _shipment_widget(data: dict[str, Any], shipment: str | None) -> dict[str, Any]:
    """The named parcel's own widget, or the first one if none was named.

    Asking for one parcel does not get a page about one parcel: Ozon serves the
    whole order and highlights the one asked for, so every parcel's widget is
    there. Taking whichever came first gave the answer another parcel's status,
    and which parcel that was changed between requests.
    """
    states = [state for state in widgets_all(data, "shipmentWidget") if isinstance(state, dict)]
    if shipment:
        named = next((state for state in states if str(state.get("shipmentId")) == str(shipment)), None)
        if named is not None:
            return named
    return states[0] if states else {}


def parse_order_detail(
    data: dict[str, Any],
    order_number: str | None = None,
    shipment: str | None = None,
) -> OrderDetail:
    """A parcel's page: where it went, how the order was paid, what was in it.

    The total and the payment method belong to the order, not to this parcel —
    Ozon states them once per order however many parcels it was split into — and
    they are reported as rendered rather than summed from the items, which would
    miss delivery and discounts.

    The items are this parcel's, filtered by the parcel they travelled in. The
    page lists every parcel of the order, so an unfiltered read hands each
    parcel of a four-parcel order all fifteen items and the order looks like it
    was delivered four times over.
    """
    parcel = _shipment_widget(data, shipment)
    total = (widget(data, "orderDoneTotal") or {}).get("total") or {}
    shipment_id = parcel.get("shipmentId")
    shipment_id = str(shipment_id) if shipment_id else None
    products = parse_order_products(data, order_number)
    if shipment_id:
        products = [product for product in products if product.shipment_id == shipment_id]
    return OrderDetail(
        order_number=order_number,
        shipment_id=shipment_id,
        status=_tagged_text(parcel.get("header") or [], _SHIPMENT_STATUS_TAG) if parcel else None,
        delivery=parse_delivery(data),
        recipient=_atom((_detail_cell(data, _RECIPIENT_WIDGET_TAG) or {}).get("subtitle")),
        paid_total=_tagged_text(total, _TOTAL_PRICE_TAG),
        payment_method=_tagged_text(total, _TOTAL_METHOD_TAG),
        products=products,
    )
