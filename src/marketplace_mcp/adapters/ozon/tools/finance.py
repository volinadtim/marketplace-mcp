"""Ozon Card balance and points."""

from marketplace_mcp.adapters.ozon.models.finance import Finances, Points
from marketplace_mcp.adapters.ozon.services import finance
from marketplace_mcp.core.dependencies import run_blocking
from marketplace_mcp.mcp_server import mcp


@mcp.tool()
async def get_finances() -> Finances:
    """Ozon Card balance and the total points. This balance is what a card
    payment draws on, so it is what decides whether pay_order() will need a
    top-up. Breakdown by point type → get_points().
    """
    return await run_blocking(finance.get_finances)


@mcp.tool()
async def get_points() -> Points:
    """Points by type (Ozon points, miles, WOW points, stars) with amounts,
    burning points, and per-store seller bonuses.
    """
    return await run_blocking(finance.get_points)
