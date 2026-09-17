from typing import Final

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

"""Prometheus instrumentation, self-contained.

Signals are taken at the transport seam rather than per tool: what actually
breaks in production is the Ozon side (antibot re-challenge, token expiry,
upstream 4xx/5xx), and that all funnels through one request path. Process-level
metrics come from prometheus_client's default collectors.
"""


METRICS_PATH: Final = "/metrics"

UPSTREAM_REQUESTS: Final = Counter(
    "marketplace_mcp_upstream_requests_total",
    "Requests to Ozon, by backend and outcome.",
    ["backend", "outcome"],
)

UPSTREAM_LATENCY: Final = Histogram(
    "marketplace_mcp_upstream_request_seconds",
    "Latency of requests to Ozon.",
    ["backend"],
)

SESSION_BOOTSTRAPS: Final = Counter(
    "marketplace_mcp_session_bootstraps_total",
    "Browser bootstraps performed to clear the antibot and harvest a session.",
    ["reason"],
)

BROWSER_ACTIVE: Final = Gauge(
    "marketplace_mcp_browser_active",
    "1 while a Chromium instance is held open, 0 when only HTTP is live.",
)


def metrics_endpoint(_request: Request) -> Response:
    """Scrape endpoint; sync so Starlette runs the blocking encode off the loop."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
