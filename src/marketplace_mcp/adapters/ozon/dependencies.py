"""The Ozon session, made once per process."""

import atexit
import contextlib
from functools import cache

from marketplace_mcp.adapters.ozon.session.transport import OzonSession


@cache
def get_session() -> OzonSession:
    """The process-wide OZON session; disposed via ``get_session.cache_clear()``.

    Closing it at process exit is not housekeeping: Chromium owns the profile
    and only writes its cookie jar out when it shuts down, so a process that
    just dies loses every token rotated during the run — and, once, a login
    that had already succeeded.
    """
    session = OzonSession()

    def flush() -> None:
        # Interpreter shutdown is a hostile place to raise from.
        with contextlib.suppress(Exception):
            session.close()

    atexit.register(flush)
    return session
