"""Fixed values of the Wildberries account API: where to ask, and what it means."""

from typing import Final

from marketplace_mcp.core.records import OrderState

HOME: Final = "https://www.wildberries.ru"
PURCHASES_PAGE: Final = f"{HOME}/lk/mypurchases"

# The archive is the better of the two histories WB serves. The other,
# astro's purchases/list, carries the same rows with terser names and no status
# word — this one states «Purchased» / «Refund» outright and prices in both
# roubles and kopecks.
ARCHIVE: Final = f"{HOME}/webapi/lk/myorders/archive/get"
ACTIVE_DELIVERIES: Final = f"{HOME}/webapi/v2/lk/myorders/delivery/active"

# Where the sign-in SDK leaves the access token. Reading it out of the browser
# profile is the only thing the browser is needed for.
TOKEN_STORAGE_KEY: Final = "wbid-oauth-sdk-access-token"  # ruff: ignore[hardcoded-password-string]

# Sent by the site on every API call; without it some services answer as though
# the caller were an app.
APP_TYPE: Final = "site"

# The public directory of pickup points, served without authorisation. An order
# states only the id of the point it went to.
OFFICES_DIRECTORY: Final = "https://static-basket-01.wbbasket.ru/vol0/data/all-poo-fr-v3.json"

# Ids at and above this are warehouses, not pickup points: the order was carried
# to a door instead of collected, and no pickup point took part.
WAREHOUSE_ID_FROM: Final = 50_000_000

# WB's own word for what became of an order line, and what it means for a store
# that only cares whether the outcome can still change. «Refund» is a line that
# arrived and went back; it is settled either way.
STATES: Final[dict[str, OrderState]] = {
    "Purchased": OrderState.RECEIVED,
    "Refund": OrderState.RECEIVED,
    "Rejected": OrderState.CANCELLED,
    "FailedPayment": OrderState.CANCELLED,
    "Canceled": OrderState.CANCELLED,
    "Cancelled": OrderState.CANCELLED,
}

# A line whose status word says it came back, whatever else it says.
RETURNED: Final = frozenset({"Refund"})
