"""What every adapter needs from the runtime, whichever site it talks to.

No module-level singletons: the factories are cached, so a test can drop one by
clearing its cache instead of reaching into another module's globals.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import cache


@cache
def get_executor() -> ThreadPoolExecutor:
    """The one thread every session operation runs on.

    Two constraints force a single dedicated thread: Playwright's sync API
    refuses to run inside a live asyncio loop (which is where the MCP server
    dispatches tools), and its objects may only be touched from the thread that
    created them — so a pool of interchangeable workers would break browser
    reuse. One worker also serialises access, matching a session's own lock.
    """
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="marketplace-session")


async def run_blocking[T](work: Callable[[], T]) -> T:
    """Await blocking session work off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(get_executor(), work)
