"""Tests for the Prometheus metrics endpoint (agent_sentry.metrics)."""

import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import pytest

from agent_sentry.cli import cmd_metrics
from agent_sentry.metrics import (
    _escape_label,
    _format_value,
    build_metrics,
    create_metrics_server,
    start_metrics_server,
)
from agent_sentry.storage import EventStore


def store_event(store, **overrides):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "function_call",
        "function_name": "my_agent",
        "success": True,
        "duration_ms": 100.0,
    }
    event.update(overrides)
    store.store_event(event)


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "metrics.db"))


@pytest.fixture
def populated_store(store):
    for i in range(8):
        store_event(store, duration_ms=100.0 + i)
    for _ in range(2):
        store_event(
            store,
            success=False,
            error_message="timed out",
            root_cause="timeout",
        )
    store_event(
        store,
        event_type="llm_call",
        function_name="openai.gpt-4o",
        args={"model": "gpt-4o"},
        cost=0.25,
        token_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )
    return store


def sample_value(text, sample):
    """Extract the value of an exact sample line from exposition text."""
    for line in text.splitlines():
        if line.startswith(sample + " "):
            return float(line.split(" ")[-1])
    raise AssertionError(f"sample {sample!r} not found in:\n{text}")


class TestEscaping:
    def test_escape_label(self):
        assert _escape_label('a"b') == 'a\\"b'
        assert _escape_label("a\\b") == "a\\\\b"
        assert _escape_label("a\nb") == "a\\nb"

    def test_format_value(self):
        assert _format_value(3.0) == "3"
        assert _format_value(3.5) == "3.5"
        assert _format_value(0) == "0"


class TestBuildMetrics:
    def test_empty_store(self, store):
        text = build_metrics(store)
        assert sample_value(text, "agent_sentry_up") == 1
        assert sample_value(text, "agent_sentry_reliability_score") == 100.0
        assert text.endswith("\n")

    def test_help_and_type_lines(self, store):
        text = build_metrics(store)
        assert "# HELP agent_sentry_up " in text
        assert "# TYPE agent_sentry_up gauge" in text
        assert "# TYPE agent_sentry_events_total counter" in text

    def test_event_counts(self, populated_store):
        text = build_metrics(populated_store)
        assert sample_value(
            text,
            'agent_sentry_events_total{event_type="function_call",status="success"}',
        ) == 8
        assert sample_value(
            text,
            'agent_sentry_events_total{event_type="function_call",status="failure"}',
        ) == 2
        assert sample_value(
            text,
            'agent_sentry_events_total{event_type="llm_call",status="success"}',
        ) == 1

    def test_failures_by_root_cause(self, populated_store):
        text = build_metrics(populated_store)
        assert sample_value(
            text, 'agent_sentry_failures_total{root_cause="timeout"}'
        ) == 2

    def test_reliability_and_duration(self, populated_store):
        text = build_metrics(populated_store)
        score = sample_value(text, "agent_sentry_reliability_score")
        assert 80 < score < 100
        assert sample_value(text, "agent_sentry_avg_duration_ms") > 0

    def test_cost_and_token_metrics(self, populated_store):
        text = build_metrics(populated_store)
        assert sample_value(
            text, 'agent_sentry_cost_usd_total{model="gpt-4o"}'
        ) == 0.25
        assert sample_value(
            text, 'agent_sentry_tokens_total{model="gpt-4o"}'
        ) == 150
        assert sample_value(
            text, 'agent_sentry_wasted_cost_usd_total{model="gpt-4o"}'
        ) == 0

    def test_wasted_cost_on_failure(self, store):
        store_event(
            store,
            success=False,
            function_name="openai.gpt-4o",
            args={"model": "gpt-4o"},
            cost=0.5,
            root_cause="rate_limit",
        )
        text = build_metrics(store)
        assert sample_value(
            text, 'agent_sentry_wasted_cost_usd_total{model="gpt-4o"}'
        ) == 0.5

    def test_label_values_escaped(self, store):
        store_event(
            store,
            success=False,
            root_cause='weird"cause',
        )
        text = build_metrics(store)
        assert 'root_cause="weird\\"cause"' in text

    def test_db_size_reported(self, populated_store):
        text = build_metrics(populated_store)
        assert sample_value(text, "agent_sentry_db_size_bytes") > 0


class TestMetricsServer:
    def test_serves_metrics(self, populated_store):
        server = start_metrics_server(populated_store, port=0)
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics"
            ) as resp:
                body = resp.read().decode("utf-8")
                assert resp.status == 200
                assert "version=0.0.4" in resp.headers["Content-Type"]
            assert "agent_sentry_up 1" in body
            assert 'root_cause="timeout"' in body
        finally:
            server.shutdown()
            server.server_close()

    def test_index_page(self, store):
        server = start_metrics_server(store, port=0)
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                assert "/metrics" in resp.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()

    def test_unknown_path_404(self, store):
        server = start_metrics_server(store, port=0)
        try:
            port = server.server_address[1]
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
            assert excinfo.value.code == 404
        finally:
            server.shutdown()
            server.server_close()

    def test_scrape_reflects_new_events(self, store):
        server = start_metrics_server(store, port=0)
        try:
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/metrics"
            with urllib.request.urlopen(url) as resp:
                first = resp.read().decode("utf-8")
            assert "agent_sentry_events_total{" not in first

            store_event(store)
            with urllib.request.urlopen(url) as resp:
                second = resp.read().decode("utf-8")
            assert sample_value(
                second,
                'agent_sentry_events_total{event_type="function_call",status="success"}',
            ) == 1
        finally:
            server.shutdown()
            server.server_close()

    def test_create_server_default_store(self, tmp_path, monkeypatch):
        import agent_sentry.storage as storage_mod

        monkeypatch.setattr(
            storage_mod, "DEFAULT_DB_PATH", str(tmp_path / "default.db")
        )
        storage_mod.reset_default_store()
        server = create_metrics_server(port=0)
        try:
            assert server.store.db_path == str(tmp_path / "default.db")
        finally:
            server.server_close()
            storage_mod.reset_default_store()


class TestMetricsCli:
    def test_prints_exposition(self, populated_store, capsys):
        class Args:
            db = populated_store.db_path
            serve = False
            limit = 100000

        cmd_metrics(Args())
        out = capsys.readouterr().out
        assert "agent_sentry_up 1" in out
        assert "# TYPE agent_sentry_events_total counter" in out
        assert 'model="gpt-4o"' in out

    def test_serve_bind_failure_exits(self, store, capsys):
        blocker = create_metrics_server(store, port=0)
        try:
            class Args:
                db = store.db_path
                serve = True
                host = "127.0.0.1"
                port = blocker.server_address[1]
                limit = 100000

            with pytest.raises(SystemExit) as excinfo:
                cmd_metrics(Args())
            assert excinfo.value.code == 1
            assert "could not bind" in capsys.readouterr().out
        finally:
            blocker.server_close()
