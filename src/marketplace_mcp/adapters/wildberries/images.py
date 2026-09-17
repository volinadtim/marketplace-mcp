"""Finding a product's picture, which WB states nowhere.

An order line names the product by number and stops there. The picture is on a
CDN whose path is computable — ``vol`` is the number over 100 000 and ``part``
over 1 000, which held for every one of 96 URLs the site itself requested — but
the CDN is split across dozens of hosts and which host holds which ``vol`` is
neither published nor stable: ranges that tables on the internet give for
basket 42 are two thousand out on today's answers.

So the host is guessed from what this account has actually seen and then
checked, walking outward to neighbours when the guess misses. Measured over one
account's 144 products: 127 right first time, 142 found within a few steps, and
2 with no picture at all — delisted, and nothing to find.
"""

import logging
from typing import Any, Final

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

logger = logging.getLogger(__name__)

_VOL_DIVISOR: Final = 100_000
_PART_DIVISOR: Final = 1_000
_SIZE: Final = "c516x688"
_MAX_BASKET: Final = 60
_SEARCH_RADIUS: Final = 8
_OK: Final = 200

# Observed ranges, from requests wildberries.ru made itself. Gappy on purpose:
# these are measurements, not a specification, and the gaps are vols this
# account never had a product in. A guess landing in a gap starts at the nearest
# basket below and the search does the rest.
_OBSERVED: Final[tuple[tuple[int, int, int], ...]] = (
    (0, 137, 1),
    (157, 263, 2),
    (424, 424, 3),
    (432, 608, 4),
    (837, 976, 5),
    (1036, 1036, 6),
    (1146, 1146, 8),
    (1172, 1280, 9),
    (1379, 1574, 10),
    (1603, 1635, 11),
    (1701, 1827, 12),
    (1944, 2042, 13),
    (2086, 2175, 14),
    (2221, 2369, 15),
    (2410, 2604, 16),
    (2744, 2748, 17),
    (2953, 2953, 18),
    (3140, 3142, 19),
    (3406, 3442, 20),
    (3606, 3653, 21),
    (3719, 3889, 22),
    (4022, 4042, 23),
    (4134, 4134, 24),
    (4453, 4453, 25),
    (4610, 4827, 26),
    (5319, 5344, 28),
    (5643, 5775, 29),
    (6218, 6218, 31),
    (7515, 7515, 35),
    (8124, 8298, 37),
    (8375, 8375, 38),
    (8871, 8871, 39),
    (9468, 9468, 40),
    (10298, 10298, 41),
    (10554, 11097, 42),
    (11756, 11756, 43),
    (12175, 12529, 44),
    (14203, 14203, 46),
)


def _first_guess(vol: int) -> int:
    """Where to start looking, from what has been seen before."""
    for low, high, basket in _OBSERVED:
        if low <= vol <= high:
            return basket
    below = [basket for low, _, basket in _OBSERVED if low <= vol]
    return max(below) if below else 1


def url_for(nm_id: int, basket: int) -> str:
    """The picture's address on a given CDN host."""
    return (
        f"https://basket-{basket:02d}.wbbasket.ru"
        f"/vol{nm_id // _VOL_DIVISOR}/part{nm_id // _PART_DIVISOR}/{nm_id}/images/{_SIZE}/1.webp"
    )


def _outward(start: int) -> list[int]:
    """The guess, then its neighbours, nearest first."""
    steps = [0] + [step for n in range(1, _SEARCH_RADIUS) for step in (n, -n)]
    return [start + step for step in steps if 1 <= start + step <= _MAX_BASKET]


class ImageFinder:
    """Resolves pictures, remembering which host held each ``vol``.

    The memory is what keeps this cheap: products bought around the same time
    share a ``vol`` range, so after a handful of lookups a whole history
    resolves on the first guess.
    """

    def __init__(self, session: Any = None, timeout: float = 12.0) -> None:
        self._http = session or requests.Session(impersonate="chrome124")
        self._timeout = timeout
        self._known: dict[int, int] = {}

    def _holds(self, url: str) -> bool:
        """Whether this host actually serves that picture.

        A host that will not answer is simply not the one, and the next is
        tried either way, so the failure needs noting and nothing more.
        """
        try:
            return self._http.head(url, timeout=self._timeout).status_code == _OK
        except RequestException as unreachable:
            # Narrow on purpose. This caught Exception once, and swallowed a
            # misspelled constant as "no picture" for every product on the
            # account — a bug reported as an empty field, which is the worst
            # way to hear about one.
            logger.debug("%s did not answer: %s", url, unreachable)
            return False

    def find(self, nm_id: int) -> str | None:
        """The picture's URL, or None when the product has none left."""
        vol = nm_id // _VOL_DIVISOR
        start = self._known.get(vol) or _first_guess(vol)
        for basket in _outward(start):
            url = url_for(nm_id, basket)
            if self._holds(url):
                self._known[vol] = basket
                return url
        logger.debug("no picture found for %s", nm_id)
        return None
