"""Prometheus metrics endpoint for agent-sentry.

Renders event store statistics in the Prometheus text exposition
format (version 0.0.4) and optionally serves them over HTTP so a
Prometheus server can scrape agent reliability, failure, cost, and
token metrics. Only aggregate numbers are exposed: no prompts,
arguments, or error messages ever leave the store.

Usage:

    agent-sentry metrics                  # print one scrape to stdout
    agent-sentry metrics --serve          # serve http://127.0.0.1:9464/metrics

Python API:

    from agent_sentry import build_metrics, create_metrics_server
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from .costs import aggregate_costs
from .storage import EventStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9464
DEFAULT_EVENT_LIMIT = 100000

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_INDEX_HTML = (
    "<html><head><title>agent-sentry exporter</title></head>"
    "<body><h1>agent-sentry exporter</h1>"
    '<p><a href="/metrics">/metrics</a></p></body></html>'
)


def _escape_label(value: str) -> str:
    """Escape a label value per the Prometheus text format rules."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    """Format a sample value, rendering integral floats without a dot."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


class _MetricFamily:
    """One metric with HELP/TYPE header and any number of samples."""

    def __init__(self, name: str, help_text: str, metric_type: str):
        self.name = name
        self.help_text = help_text
        self.metric_type = metric_type
        self.samples: List[Tuple[Dict[str, str], float]] = []

    def add(self, value: float, **labels: str) -> None:
        self.samples.append((labels, value))

    def render(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} {self.metric_type}",
        ]
        for labels, value in self.samples:
            if labels:
                label_str = ",".join(
                    f'{key}="{_escape_label(str(val))}"'
                    for key, val in sorted(labels.items())
                )
                lines.append(f"{self.name}{{{label_str}}} {_format_value(value)}")
            else:
                lines.append(f"{self.name} {_format_value(value)}")
        return lines


def build_metrics(
    store: EventStore, event_limit: int = DEFAULT_EVENT_LIMIT
) -> str:
    """Render the event store as Prometheus text exposition format.

    Counters are cumulative over the whole store, so repeated scrapes
    behave like normal Prometheus counters as events accumulate
    (clearing the store resets them, which Prometheus handles as a
    counter reset). event_limit caps how many events are scanned for
    the per-model cost and token metrics.
    """
    health = store.health_check()
    up = 1.0 if health.get("status") == "healthy" else 0.0

    up_metric = _MetricFamily(
        "agent_sentry_up",
        "Whether the agent-sentry event store is reachable and healthy.",
        "gauge",
    )
    up_metric.add(up)

    families = [up_metric]

    db_size = _MetricFamily(
        "agent_sentry_db_size_bytes",
        "Size of the agent-sentry SQLite database file in bytes.",
        "gauge",
    )
    db_size.add(float(health.get("db_size_bytes") or 0))
    families.append(db_size)

    if up:
        events_total = _MetricFamily(
            "agent_sentry_events_total",
            "Events recorded, by event type and status.",
            "counter",
        )
        for event_type, counts in sorted(
            store.get_event_type_breakdown().items()
        ):
            events_total.add(
                float(counts.get("success", 0)),
                event_type=event_type,
                status="success",
            )
            events_total.add(
                float(counts.get("failure", 0)),
                event_type=event_type,
                status="failure",
            )
        families.append(events_total)

        failures_total = _MetricFamily(
            "agent_sentry_failures_total",
            "Failed events, by classified root cause.",
            "counter",
        )
        for root_cause, count in sorted(store.get_failure_breakdown().items()):
            failures_total.add(float(count), root_cause=root_cause)
        families.append(failures_total)

        reliability = _MetricFamily(
            "agent_sentry_reliability_score",
            "Success rate of all recorded events, from 0 to 100.",
            "gauge",
        )
        reliability.add(float(store.get_reliability_score()))
        families.append(reliability)

        summary = store.get_summary()
        avg_duration = _MetricFamily(
            "agent_sentry_avg_duration_ms",
            "Average duration of recorded events in milliseconds.",
            "gauge",
        )
        avg_duration.add(float(summary.get("avg_duration_ms") or 0.0))
        families.append(avg_duration)

        events = store.get_events(limit=event_limit)
        cost_total = _MetricFamily(
            "agent_sentry_cost_usd_total",
            "Estimated LLM spend in US dollars, by model.",
            "counter",
        )
        wasted_total = _MetricFamily(
            "agent_sentry_wasted_cost_usd_total",
            "Estimated spend on failed calls in US dollars, by model.",
            "counter",
        )
        tokens_total = _MetricFamily(
            "agent_sentry_tokens_total",
            "Total tokens used, by model.",
            "counter",
        )
        for bucket in aggregate_costs(events, by="model"):
            cost_total.add(round(bucket.cost, 6), model=bucket.key)
            wasted_total.add(round(bucket.wasted_cost, 6), model=bucket.key)
            tokens_total.add(float(bucket.tokens), model=bucket.key)
        families.extend([cost_total, wasted_total, tokens_total])

    lines: List[str] = []
    for family in families:
        lines.extend(family.render())
    return "\n".join(lines) + "\n"


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serves / (index) and /metrics from the attached event store."""

    server: "MetricsServer"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/metrics":
            try:
                body = build_metrics(
                    self.server.store, self.server.event_limit
                ).encode("utf-8")
            except Exception as exc:  # pragma: no cover - defensive
                self._respond(500, "text/plain", f"error: {exc}".encode())
                return
            self._respond(200, CONTENT_TYPE, body)
        elif path == "/":
            self._respond(200, "text/html; charset=utf-8", _INDEX_HTML.encode("utf-8"))
        else:
            self._respond(404, "text/plain", b"not found; try /metrics")

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence per-request logging (scrapes are frequent)."""


class MetricsServer(ThreadingHTTPServer):
    """HTTP server exposing an event store at /metrics."""

    daemon_threads = True

    def __init__(
        self,
        store: EventStore,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        event_limit: int = DEFAULT_EVENT_LIMIT,
    ):
        self.store = store
        self.event_limit = event_limit
        super().__init__((host, port), _MetricsHandler)


def create_metrics_server(
    store: Optional[EventStore] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> MetricsServer:
    """Create (but do not start) a Prometheus metrics server.

    Call serve_forever() to block, or start it on a thread:

        server = create_metrics_server(port=9464)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    """
    if store is None:
        from .storage import get_store

        store = get_store()
    return MetricsServer(store, host=host, port=port, event_limit=event_limit)


def start_metrics_server(
    store: Optional[EventStore] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> MetricsServer:
    """Create a metrics server and start it on a daemon thread.

    Returns the running server; call shutdown() to stop it. Handy for
    exposing metrics from inside an agent process:

        import agent_sentry
        agent_sentry.start_metrics_server(port=9464)
    """
    server = create_metrics_server(
        store, host=host, port=port, event_limit=event_limit
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
