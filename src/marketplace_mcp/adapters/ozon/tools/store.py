"""Keeping a local copy of the account, and asking it what Ozon cannot answer."""

from typing import Annotated, Any, Final

from pydantic import Field

from marketplace_mcp.adapters.ozon.source import OzonSource
from marketplace_mcp.core.dependencies import run_blocking
from marketplace_mcp.core.store import connect
from marketplace_mcp.core.store.dedup import link_duplicates as _link_duplicates
from marketplace_mcp.core.store.sync import sync_orders as _sync_orders, sync_prices as _sync_prices
from marketplace_mcp.core.store.writes import now as _now
from marketplace_mcp.mcp_server import mcp
from marketplace_mcp.settings import get_settings

SOURCE: Final = OzonSource()


def _run(work: Any) -> Any:
    """Open the store for one call, hand it to the work, close it again.

    A connection per call rather than one held open: the syncs run on the
    session thread while a reader may be on another, and SQLite connections are
    not to be shared across threads.
    """
    connection = connect(get_settings().store_path)
    try:
        return work(connection)
    finally:
        connection.close()


@mcp.tool()
async def sync_prices(
    limit: Annotated[int, Field(ge=1, le=1000, description="How much of the purchase history to walk.")] = 1000,
) -> dict[str, Any]:
    """Walk the purchase history into the local store and record every price
    with the time it was read.
    The price recorded is Ozon's price *today* for a product that may have been
    bought years ago — the series, not what was paid. What was paid comes from
    the order and is stored by sync_orders().
    Cheap: one walk, about a minute for a thousand items, no order pages. Safe to
    run daily — that is what builds a price history, since Ozon keeps none.
    """
    report = await run_blocking(lambda: _run(lambda store: _sync_prices(store, SOURCE, limit)))
    return report.__dict__


@mcp.tool()
async def sync_orders(
    full: Annotated[bool, Field(description="Re-read every order instead of stopping at a stored one.")] = False,
    limit: Annotated[int, Field(ge=1, le=1000, description="How many orders to walk at most.")] = 1000,
) -> dict[str, Any]:
    """Walk orders into the local store: status and date, the parcels each was
    split into, where every parcel went, what was paid and what was in it.
    Expensive — a request per parcel — so a first run over a few hundred orders
    takes tens of minutes. After that it stops at the first order already stored
    and settled («Получен» / «Отменён»), which makes a routine run short. Active
    orders are re-read every time: their parcels are still moving.
    `full` re-reads everything regardless; use it for a first sync or after a
    schema change. `stopped_at` in the answer says which stored order ended the
    walk, or is null if it reached the end of the history.
    """
    report = await run_blocking(lambda: _run(lambda store: _sync_orders(store, SOURCE, full=full, limit=limit)))
    return report.__dict__


@mcp.tool()
async def store_stats() -> dict[str, Any]:
    """What the local store holds: row counts, the span of the price history and
    the last few syncs.
    Answer this before a sync to see whether one is needed, and after one to see
    what it did.
    """

    def counts(store: Any) -> dict[str, Any]:
        # Counted per marketplace, not across the store: this tool answers for
        # Ozon, and the same file will hold other marketplaces beside it.
        tables = ("items", "orders", "parcels", "order_items", "price_observations")
        out: dict[str, Any] = {
            table: store.execute(
                f"SELECT count(*) AS n FROM {table} WHERE marketplace = ?",  # ruff: ignore[hardcoded-sql-expression]
                (SOURCE.name,),
            ).fetchone()["n"]
            for table in tables
        }
        span = store.execute(
            "SELECT min(observed_at) AS a, max(observed_at) AS b FROM price_observations WHERE marketplace = ?",
            (SOURCE.name,),
        ).fetchone()
        out["prices_from"], out["prices_to"] = span["a"], span["b"]
        out["marketplace"] = SOURCE.name
        out["path"] = str(get_settings().store_path)
        out["recent_syncs"] = [
            dict(row)
            for row in store.execute(
                "SELECT started_at, finished_at, kind, orders_seen, orders_read, items_seen, note"
                " FROM sync_runs WHERE marketplace = ? ORDER BY started_at DESC LIMIT 5",
                (SOURCE.name,),
            )
        ]
        return out

    return await run_blocking(lambda: _run(counts))


@mcp.tool()
async def link_duplicates() -> dict[str, Any]:
    """Find the same product stored under two skus and link them — without
    merging anything.
    Ozon reissues a card with a new sku and the old one stays in the purchase
    history, so one thing bought twice reads as two products: one with the photo
    and no purchase, one with the purchase and no photo.
    Only groups where no sku states a variant are linked. A title is shared by a
    size and a colour as readily as by a reissue («Шорты DARE» in 46 and in 48),
    and those are two garments — they are recorded as examined, with the variants
    that decided it, and left apart.
    Nothing is deleted or rewritten: the links live in their own table, so
    reading through them is the caller's choice and a wrong call is undone by
    dropping a row.
    """
    return await run_blocking(lambda: _run(lambda store: _link_duplicates(store, SOURCE.name, _now())))
