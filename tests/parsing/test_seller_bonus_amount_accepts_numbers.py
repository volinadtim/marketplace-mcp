"""Ozon types the same field two ways, so the model must take both."""

from __future__ import annotations

from marketplace_mcp.adapters.ozon.models.finance import SellerBonus


def test_seller_bonus_amount_accepts_numbers() -> None:
    assert SellerBonus(seller="LOFT52", amount=1294).amount == "1294"
    assert SellerBonus(seller="LOFT52", amount="1 294").amount == "1 294"
    assert SellerBonus(seller="LOFT52").amount is None
