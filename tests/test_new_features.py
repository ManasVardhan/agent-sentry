"""Tests for new features: webhook retry, health_check, get_summary, CLI commands."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from agent_sentry.alerts import WebhookAlert, CallbackAlert, AlertManager
from agent_sentry.storage import EventStore


# ---------------------------------------------------------------------------
# WebhookAlert retry logic
# ---------------------------------------------------------------------------


class TestWebhookRetry:
    """Tests for WebhookAlert retry with exponential backoff."""

    def _make_event(self) -> dict:
        return {
            "event_id": "test-001",
            "timestamp": "2026-01-01T00:00:00Z",
            "function_name": "my_agent",
            "error_message": "Something broke",
            "error_type": "RuntimeError",
            "root_cause": "unknown",
            "duration_ms": 150.0,
            "success": False,
        }

    @patch("agent_sentry.alerts.urlopen")
    def test_success_on_first_try(self, mock_urlopen: MagicMock) -> None:
        """No retries needed when first request succeeds."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        alert = WebhookAlert("http://example.com/hook", max_retries=3)
        result = alert.send(self._make_event())
        assert result is True
        assert mock_urlopen.call_count == 1

    @patch("agent_sentry.alerts.time.sleep")
    @patch("agent_sentry.alerts.urlopen")
    def test_retry_on_network_error(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Retries on URLError and eventually succeeds."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        # Fail twice, succeed on third
        mock_urlopen.side_effect = [
            URLError("Connection refused"),
            URLError("Connection refused"),
            mock_resp,
        ]

        alert = WebhookAlert("http://example.com/hook", max_retries=3, base_delay=0.01)
        result = alert.send(self._make_event())
        assert result is True
        assert mock_urlopen.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("agent_sentry.alerts.time.sleep")
    @patch("agent_sentry.alerts.urlopen")
    def test_all_retries_exhausted(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Returns False when all retries are exhausted."""
        mock_urlopen.side_effect = URLError("Connection refused")

        alert = WebhookAlert("http://example.com/hook", max_retries=2, base_delay=0.01)
        result = alert.send(self._make_event())
        assert result is False
        assert mock_urlopen.call_count == 3  # initial + 2 retries

    @patch("agent_sentry.alerts.urlopen")
    def test_no_retries_when_disabled(self, mock_urlopen: MagicMock) -> None:
        """max_retries=0 means no retry attempts."""
        mock_urlopen.side_effect = URLError("Connection refused")

        alert = WebhookAlert("http://example.com/hook", max_retries=0)
        result = alert.send(self._make_event())
        assert result is False
        assert mock_urlopen.call_count == 1

    @patch("agent_sentry.alerts.time.sleep")
    @patch("agent_sentry.alerts.urlopen")
    def test_exponential_backoff_delays(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Verify exponential backoff delay pattern."""
        mock_urlopen.side_effect = URLError("fail")

        alert = WebhookAlert("http://example.com/hook", max_retries=3, base_delay=1.0)
        alert.send(self._make_event())

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]  # 1*2^0, 1*2^1, 1*2^2

    @patch("agent_sentry.alerts.urlopen")
    def test_client_error_no_retry(self, mock_urlopen: MagicMock) -> None:
        """4xx errors should not trigger retries (not transient)."""
        mock_resp = MagicMock()
        mock_resp.status = 400
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        alert = WebhookAlert("http://example.com/hook", max_retries=3)
        result = alert.send(self._make_event())
        assert result is False
        assert mock_urlopen.call_count == 1

    @patch("agent_sentry.alerts.urlopen")
    def test_custom_timeout(self, mock_urlopen: MagicMock) -> None:
        """Custom timeout is passed to urlopen."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        alert = WebhookAlert("http://example.com/hook", timeout=30)
        alert.send(self._make_event())

        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 30

    @patch("agent_sentry.alerts.urlopen")
    def test_custom_headers_sent(self, mock_urlopen: MagicMock) -> None:
        """Custom headers are included in the request."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        alert = WebhookAlert(
            "http://example.com/hook",
            headers={"Authorization": "Bearer test-token"},
        )
        alert.send(self._make_event())

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer test-token"


# ---------------------------------------------------------------------------
# EventStore.health_check()
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for EventStore.health_check()."""

    def test_healthy_empty_store(self, tmp_path: str) -> None:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        result = store.health_check()

        assert result["status"] == "healthy"
        assert result["event_count"] == 0
        assert result["writable"] is True
        assert result["db_path"] == db
        assert result["db_size_bytes"] > 0  # SQLite creates a file

    def test_healthy_with_events(self, tmp_path: str) -> None:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        store.store_event({
            "event_id": "e1",
            "event_type": "function_call",
            "success": True,
        })
        result = store.health_check()
        assert result["status"] == "healthy"
        assert result["event_count"] == 1

    def test_health_check_returns_dict(self, tmp_path: str) -> None:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        result = store.health_check()
        assert isinstance(result, dict)
        assert set(result.keys()) >= {"status", "db_path", "event_count", "db_size_bytes", "writable"}


# ---------------------------------------------------------------------------
# EventStore.get_summary()
# ---------------------------------------------------------------------------


class TestGetSummary:
    """Tests for EventStore.get_summary()."""

    def _make_store(self, tmp_path: str) -> EventStore:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        # Add mixed events
        store.store_event({
            "event_id": "s1", "event_type": "function_call",
            "success": True, "duration_ms": 100.0,
        })
        store.store_event({
            "event_id": "s2", "event_type": "function_call",
            "success": True, "duration_ms": 200.0,
        })
        store.store_event({
            "event_id": "f1", "event_type": "llm_call",
            "success": False, "root_cause": "timeout", "duration_ms": 5000.0,
            "error_message": "Request timed out",
        })
        store.store_event({
            "event_id": "f2", "event_type": "tool_call",
            "success": False, "root_cause": "rate_limit", "duration_ms": 300.0,
            "error_message": "Rate limited",
        })
        return store

    def test_summary_totals(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        summary = store.get_summary()
        assert summary["total_events"] == 4
        assert summary["failures"] == 2

    def test_summary_reliability(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        summary = store.get_summary()
        assert summary["reliability_score"] == 50.0

    def test_summary_avg_duration(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        summary = store.get_summary()
        # (100 + 200 + 5000 + 300) / 4 = 1400
        assert summary["avg_duration_ms"] == 1400.0

    def test_summary_root_causes(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        summary = store.get_summary()
        assert "timeout" in summary["top_root_causes"]
        assert "rate_limit" in summary["top_root_causes"]

    def test_summary_event_types(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        summary = store.get_summary()
        assert "function_call" in summary["event_types"]
        assert summary["event_types"]["function_call"]["success"] == 2

    def test_summary_empty_store(self, tmp_path: str) -> None:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        summary = store.get_summary()
        assert summary["total_events"] == 0
        assert summary["failures"] == 0
        assert summary["reliability_score"] == 100.0
        assert summary["avg_duration_ms"] is None

    def test_summary_with_since_filter(self, tmp_path: str) -> None:
        store = self._make_store(str(tmp_path))
        # Future date should match nothing
        summary = store.get_summary(since="2099-01-01T00:00:00Z")
        assert summary["total_events"] == 0

    def test_summary_top_causes_limited_to_5(self, tmp_path: str) -> None:
        db = os.path.join(str(tmp_path), "test.db")
        store = EventStore(db_path=db)
        for i in range(10):
            store.store_event({
                "event_id": f"e{i}", "event_type": "call",
                "success": False, "root_cause": f"cause_{i}",
            })
        summary = store.get_summary()
        assert len(summary["top_root_causes"]) <= 5


# ---------------------------------------------------------------------------
# CLI: health command
# ---------------------------------------------------------------------------


class TestCLIHealth:
    """Tests for the 'health' CLI command."""

    def test_health_exits_zero(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "agent_sentry", "--db", db_path, "health"],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0
            assert "healthy" in result.stdout.lower() or "OK" in result.stdout
        finally:
            os.unlink(db_path)

    def test_health_shows_writable(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "agent_sentry", "--db", db_path, "health"],
                capture_output=True, text=True, timeout=10,
            )
            assert "yes" in result.stdout.lower()
        finally:
            os.unlink(db_path)


class TestCLISummary:
    """Tests for the 'summary' CLI command."""

    def test_summary_exits_zero(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "agent_sentry", "--db", db_path, "summary"],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0
            assert "Summary" in result.stdout or "Events" in result.stdout
        finally:
            os.unlink(db_path)

    def test_summary_with_hours(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "agent_sentry", "--db", db_path, "summary", "--hours", "24"],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0
            assert "24h" in result.stdout or "24" in result.stdout
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# AlertManager with sync mode
# ---------------------------------------------------------------------------


class TestAlertManagerSync:
    """Tests for AlertManager synchronous alert sending."""

    def test_sync_sends_immediately(self) -> None:
        received = []
        cb = CallbackAlert(lambda e: received.append(e))
        mgr = AlertManager(channels=[cb], async_send=False)
        mgr.send_alert({"success": False, "function_name": "test"})
        assert len(received) == 1

    def test_sync_skips_success(self) -> None:
        received = []
        cb = CallbackAlert(lambda e: received.append(e))
        mgr = AlertManager(channels=[cb], async_send=False)
        mgr.send_alert({"success": True})
        assert len(received) == 0

    def test_multiple_channels(self) -> None:
        r1, r2 = [], []
        mgr = AlertManager(
            channels=[
                CallbackAlert(lambda e, r=r1: r.append(e)),
                CallbackAlert(lambda e, r=r2: r.append(e)),
            ],
            async_send=False,
        )
        mgr.send_alert({"success": False})
        assert len(r1) == 1
        assert len(r2) == 1

    def test_channel_error_doesnt_stop_others(self) -> None:
        received = []

        def failing_cb(e: dict) -> None:
            raise RuntimeError("boom")

        mgr = AlertManager(
            channels=[
                CallbackAlert(failing_cb),
                CallbackAlert(lambda e: received.append(e)),
            ],
            async_send=False,
        )
        mgr.send_alert({"success": False})
        # Second channel still received the alert
        assert len(received) == 1
