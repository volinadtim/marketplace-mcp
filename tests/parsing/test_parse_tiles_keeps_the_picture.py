"""A tile's picture is read, not dropped.

Ozon sends one with every purchases tile, so a whole purchase history comes
back with pictures in a single walk — a product card per item would cost a
request each.
"""

from __future__ import annotations

import json

from marketplace_mcp.adapters.ozon.parsing.catalog import parse_tiles

COVER = "https://ir.ozone.ru/s3/multimedia-1-4/9721855912.jpg"
SECOND = "https://ir.ozone.ru/s3/multimedia-1-t/8058369017.jpg"


def _grid(tile: dict[str, object]) -> dict[str, dict[str, str]]:
    grid = {"items": [tile]}
    return {"widgetStates": {"tileGridDesktop-1-default-1": json.dumps(grid, ensure_ascii=False)}}


def test_the_cover_is_the_first_picture() -> None:
    tile = {
        "action": {"link": "/product/salfetki-2904652891/"},
        "tileImage": {"items": [{"image": {"link": COVER}}, {"image": {"link": SECOND}}]},
    }
    assert parse_tiles(_grid(tile))[0].image == COVER


def test_a_tile_without_a_picture_says_so() -> None:
    tile = {"action": {"link": "/product/salfetki-2904652891/"}}
    assert parse_tiles(_grid(tile))[0].image is None
