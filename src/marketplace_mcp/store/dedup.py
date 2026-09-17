"""Finding the same product hiding under two skus — and refusing to when it is not.

Ozon reissues a product card and gives it a new sku. The old one stays in the
purchase history, so a thing bought twice over two years arrives as two
products, one with the photo and no purchase, the other with the purchase and
no photo. Left alone it becomes two rows in an inventory of one jar of granola.

The tempting fix — same title, same product — is wrong, and wrong in a way that
loses more than it fixes. On this account thirteen of the forty-one title
collisions are a size or a colour apart:

    шорты dare         46 RU / 31         vs  48 RU / 32
    футболка rizziano  48 RU/M Белоснежный vs 48 RU/M Угольный чёрный
    кроссовки adidas   40,5 RU / UK 8     vs  41 RU / UK 8,5

Those are two garments, owned separately, and merging them deletes one. So a
group is left apart when its skus state variants that *differ*, and linked
otherwise. What matters is the disagreement, not the presence: a lens set sold
only in "Черный" states a variant on both of its skus and is still one product,
and refusing to link it because a colour was named would leave the duplicate in
place for no reason.
"""

import re
from collections import Counter, defaultdict
from typing import Any

_PUNCTUATION = re.compile(r"[«»\"'`,.:;()\[\]/\\|—–-]")
_COUNT = re.compile(r"\b\d+\s*шт\b")
_SPACES = re.compile(r"\s+")

LINKED = "same-title-no-variant"
KEPT_APART = "different-variants"
ADJACENT = "adjacent-skus"
SKIPPED = "unidentifying-title"

# Ozon numbers the variants of one card together, while a reissue years later
# gets whatever number is current. So skus a few hundred apart were created in
# one go — a size run, a colour range — whatever their titles say. Measured on
# this account: the groups proven to be variants sit 9, 34, 144, 404 and 946
# apart, and no group that proved to be a genuine reissue came closer than
# 2,950. That is one account's gap, so this stays a heuristic — and it only ever
# refuses to link, never links.
ADJACENT_SKU_SPAN = 1000

# Titles that identify nothing: Ozon prints the first in place of a name for a
# product it will not show here, and unrelated products then share it.
_NOT_A_TITLE = ("недоступно в вашем регионе",)


def normalise(title: str | None) -> str:
    """A title reduced to what is stable across a reissue.

    Case, punctuation and "1 шт" go: Ozon rewrites all three between cards for
    the same thing. Everything else stays, because it is the only evidence of
    what the product actually is.
    """
    text = (title or "").lower().replace("ё", "е")
    text = _PUNCTUATION.sub(" ", text)
    text = _COUNT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _known_titles(connection: Any) -> dict[str, str]:
    """Every sku with the best title known for it.

    The purchase list's title wins over an order's: it is the card's current
    name, and orders keep whatever the name was on the day.
    """
    titles: dict[str, str] = {}
    for row in connection.execute("SELECT sku, title FROM order_items WHERE title IS NOT NULL"):
        titles.setdefault(row["sku"], row["title"])
    for row in connection.execute("SELECT sku, title FROM items WHERE title IS NOT NULL"):
        titles[row["sku"]] = row["title"]
    return titles


def _variants(connection: Any) -> dict[str, set[str]]:
    """The variants each sku was ever ordered in."""
    out: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute("SELECT sku, variant FROM order_items WHERE variant IS NOT NULL"):
        out[row["sku"]].add(row["variant"])
    return out


def _identifies_a_product(title: str) -> bool:
    """Whether a normalised title says which product this is.

    One word is a category, not an identity — four unrelated t-shirts on this
    account are all "футболка" — and Ozon's region placeholder is not a name at
    all. Matching on either joins things with nothing in common.
    """
    return title not in _NOT_A_TITLE and len(title.split()) > 1


def _created_together(skus: set[str]) -> bool:
    """Whether these skus were numbered as the variants of one listing."""
    numbers = sorted(int(sku) for sku in skus if sku.isdigit())
    return len(numbers) == len(skus) and numbers[-1] - numbers[0] <= ADJACENT_SKU_SPAN


def _canonical(skus: set[str], complete: set[str]) -> str:
    """Which sku of a group the others should point at.

    The one Ozon still lists *and* has an order for is worth the most — it
    carries both the photo and what was paid — so it wins. Otherwise the
    highest sku, which is Ozon's newest card for the thing, and a stable choice
    so that running this twice does not reverse the arrows.
    """
    both = sorted(skus & complete)
    return both[-1] if both else max(skus)


def _judge(key: str, skus: set[str], variants: dict[str, set[str]]) -> tuple[str, str]:
    """What this group of same-titled skus actually is, and why.

    The order matters: every check below is a reason *not* to link, and they are
    asked before the one that does.
    """
    if not _identifies_a_product(key):
        return SKIPPED, "the title identifies no particular product"
    stated = {variant for sku in skus for variant in variants.get(sku, set())}
    if len(stated) > 1:
        return KEPT_APART, "; ".join(sorted(stated))[:200]
    if _created_together(skus):
        span = max(int(sku) for sku in skus) - min(int(sku) for sku in skus)
        return ADJACENT, f"{span} apart — numbered as one listing"
    return LINKED, key[:200]


def link_duplicates(connection: Any, at: str) -> dict[str, Any]:
    """Link skus that are the same product; record the ones deliberately not.

    Returns what it did and what it refused to do, because the refusals are the
    interesting half: they are the pairs a title-only rule would have merged.
    """
    titles = _known_titles(connection)
    variants = _variants(connection)
    listed = {row["sku"] for row in connection.execute("SELECT sku FROM items")}
    ordered = {row["sku"] for row in connection.execute("SELECT DISTINCT sku FROM order_items")}
    complete = listed & ordered

    groups: dict[str, set[str]] = defaultdict(set)
    for sku, title in titles.items():
        key = normalise(title)
        if key:
            groups[key].add(sku)

    linked: list[tuple[str, str, str, str | None, str]] = []
    verdicts: Counter[str] = Counter()
    for key, skus in groups.items():
        if len(skus) < 2:
            continue
        method, note = _judge(key, skus, variants)
        verdicts[method] += 1
        if method == SKIPPED:
            continue
        canonical = _canonical(skus, complete) if method == LINKED else None
        linked.extend((sku, canonical or sku, method, note, at) for sku in sorted(skus))

    connection.executemany(
        """
        INSERT INTO item_links (sku, canonical_sku, method, note, linked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(sku) DO UPDATE SET
            canonical_sku = excluded.canonical_sku,
            method        = excluded.method,
            note          = excluded.note,
            linked_at     = excluded.linked_at
        """,
        linked,
    )
    connection.commit()

    merged = sum(1 for sku, canonical, method, _, _ in linked if method == LINKED and sku != canonical)
    collisions = sum(1 for skus in groups.values() if len(skus) > 1)
    return {
        "skus_known": len(titles),
        "titles": len(groups),
        "collisions": collisions,
        "groups_linked": verdicts[LINKED],
        "skus_merged_away": merged,
        "groups_kept_apart": verdicts[KEPT_APART],
        "groups_adjacent_skus": verdicts[ADJACENT],
        "groups_unidentifying_title": verdicts[SKIPPED],
    }
