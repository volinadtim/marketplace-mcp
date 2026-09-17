"""Finance services: card balance and points."""

from marketplace_mcp.dependencies import get_session
from marketplace_mcp.models.finance import Finances, Points
from marketplace_mcp.parsing.finance import parse_finance, parse_points


def get_finances() -> Finances:
    return parse_finance(get_session().fetch("/my/main"))


def get_points() -> Points:
    return parse_points(get_session().fetch("/my/points"))
