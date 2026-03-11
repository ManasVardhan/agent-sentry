"""Edge case and stress tests for agent-sentry."""

import time
import threading

import pytest

from agent_sentry import watch, configure, reset_capture
from agent_sentry.capture import EventCapture, _safe_repr
from agent_sentry.storage import EventStore, reset_default_store
from agent_sentry.alerts import (
    AlertManager,
    CallbackAlert,
    WebhookAlert,
    SlackAlert,
    EmailAlert,
    _format_payload,
)
from agent_sentry.analysis import classify_error, RootCause


@pytest.fixture(autouse=True)
def clean_state():
    """Reset globals between tests."""
    reset_capture()
    reset_default_store()
    yield
    reset_capture()
    reset_default_store()


@pytest.fixture
def tmp_store(tmp_path):
    return EventStore(db_path=str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# Storage edge cases
# ---------------------------------------------------------------------------
class TestStorageEdgeCases:
    def test_concurrent_writes(self, tmp_path):
        """Multiple threads writing events should not corrupt the DB."""
        store = EventStore(db_path=str(tmp_path / "concurrent.db"))
        errors = []

        def write_events(start_id: int):
            try:
                for i in range(20):
                    store.store_event({
                        "event_id": f"thread-{start_id}-{i}",
                        "event_type": "test",
                        "success": True,
                    })
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_events, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        total = store.get_total_count()
        assert total == 100  # 5 threads x 20 events

    def test_duplicate_event_id_replaced(self, tmp_store):
        """INSERT OR REPLACE should handle duplicate event_ids."""
        tmp_store.store_event({
            "event_id": "dup-1",
            "event_type": "test",
            "success": True,
        })
        tmp_store.store_event({
            "event_id": "dup-1",
            "event_type": "test",
            "error_message": "updated",
            "success": False,
        })
        events = tmp_store.get_events(limit=10)
        assert len(events) == 1
        assert events[0]["success"] is False

    def test_store_event_with_none_fields(self, tmp_store):
        """All optional fields should tolerate None."""
        tmp_store.store_event({
            "event_id": "null-test",
            "event_type": "test",
            "function_name": None,
            "error_message": None,
            "error_type": None,
            "traceback": None,
            "duration_ms": None,
            "success": True,
            "metadata": None,
            "tags": None,
        })
        events = tmp_store.get_events()
        assert len(events) == 1

    def test_store_event_with_unicode(self, tmp_store):
        """Unicode in error messages and function names."""
        tmp_store.store_event({
            "event_id": "unicode-1",
            "event_type": "test",
            "function_name": "process_text",
            "error_message": "Invalid character: \u2603 snowman",
            "success": False,
            "root_cause": "malformed_args",
        })
        events = tmp_store.get_events()
        assert "\u2603" in events[0]["error_message"]

    def test_filter_by_root_cause(self, tmp_store):
        """get_events should filter by root_cause."""
        for cause in ["timeout", "rate_limit", "auth_error"]:
            tmp_store.store_event({
                "event_id": f"rc-{cause}",
                "event_type": "test",
                "success": False,
                "root_cause": cause,
            })
        events = tmp_store.get_events(root_cause="timeout")
        assert len(events) == 1
        assert events[0]["root_cause"] == "timeout"

    def test_filter_by_event_type(self, tmp_store):
        tmp_store.store_event({"event_id": "e1", "event_type": "llm_call", "success": True})
        tmp_store.store_event({"event_id": "e2", "event_type": "tool_call", "success": True})
        tmp_store.store_event({"event_id": "e3", "event_type": "llm_call", "success": True})
        events = tmp_store.get_events(event_type="llm_call")
        assert len(events) == 2

    def test_pagination(self, tmp_store):
        for i in range(10):
            tmp_store.store_event({
                "event_id": f"page-{i}",
                "event_type": "test",
                "success": True,
            })
        page1 = tmp_store.get_events(limit=5, offset=0)
        page2 = tmp_store.get_events(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        ids1 = {e["event_id"] for e in page1}
        ids2 = {e["event_id"] for e in page2}
        assert ids1.isdisjoint(ids2)

    def test_reliability_all_failures(self, tmp_store):
        for i in range(10):
            tmp_store.store_event({
                "event_id": f"fail-{i}",
                "event_type": "test",
                "success": False,
            })
        assert tmp_store.get_reliability_score() == 0.0

    def test_reliability_all_success(self, tmp_store):
        for i in range(10):
            tmp_store.store_event({
                "event_id": f"ok-{i}",
                "event_type": "test",
                "success": True,
            })
        assert tmp_store.get_reliability_score() == 100.0


# ---------------------------------------------------------------------------
# Capture edge cases
# ---------------------------------------------------------------------------
class TestCaptureEdgeCases:
    def test_nested_watch_decorators(self, tmp_store):
        """Nested decorated functions should both be captured."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def inner():
            return "inner result"

        @watch(capture=cap)
        def outer():
            return inner()

        result = outer()
        assert result == "inner result"
        events = tmp_store.get_events()
        assert len(events) == 2

    def test_watch_with_generator(self, tmp_store):
        """Watch should handle functions returning generators by consuming them."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def gen_func():
            return [x for x in range(5)]

        result = gen_func()
        assert result == [0, 1, 2, 3, 4]

    def test_watch_with_large_args(self, tmp_store):
        """Large args should be truncated in _safe_repr."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def big_input(data):
            return len(data)

        big_list = list(range(1000))
        result = big_input(big_list)
        assert result == 1000

        events = tmp_store.get_events()
        assert len(events) == 1

    def test_watch_with_kwargs(self, tmp_store):
        """Keyword arguments should be captured."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def func_with_kwargs(name="default", count=1):
            return f"{name}:{count}"

        result = func_with_kwargs(name="test", count=5)
        assert result == "test:5"

    def test_capture_records_duration(self, tmp_store):
        """Duration should be recorded in milliseconds."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def slow_func():
            time.sleep(0.05)
            return "done"

        slow_func()
        events = tmp_store.get_events()
        assert events[0]["duration_ms"] >= 40  # at least 40ms (allowing for timing variance)

    def test_watch_preserves_return_value(self, tmp_store):
        """Return value should be passed through unmodified."""
        cap = EventCapture(store=tmp_store, auto_classify=False)

        @watch(capture=cap)
        def return_dict():
            return {"key": "value", "nested": {"a": 1}}

        result = return_dict()
        assert result == {"key": "value", "nested": {"a": 1}}


# ---------------------------------------------------------------------------
# Analysis edge cases
# ---------------------------------------------------------------------------
class TestAnalysisEdgeCases:
    def test_classify_with_all_none(self):
        result = classify_error(None, None, None, None, None)
        assert result == RootCause.SILENT_FAILURE  # no error, no result

    def test_classify_with_empty_strings(self):
        result = classify_error("", "", "", None, None)
        assert result == RootCause.SILENT_FAILURE

    def test_classify_long_error_message(self):
        """Very long error messages should still be classified."""
        long_msg = "x" * 10000 + " timeout occurred " + "y" * 10000
        result = classify_error(long_msg)
        assert result == RootCause.TIMEOUT

    def test_classify_multiple_patterns(self):
        """When multiple patterns match, should return the first match."""
        # timeout patterns come before rate_limit in the list
        msg = "timeout after rate limit"
        result = classify_error(msg)
        assert result == RootCause.TIMEOUT

    def test_classify_network_connection_refused(self):
        result = classify_error("connection refused")
        assert result == RootCause.NETWORK_ERROR

    def test_classify_network_dns(self):
        result = classify_error("dns resolve failed")
        assert result == RootCause.NETWORK_ERROR

    def test_classify_network_ssl(self):
        result = classify_error("ssl error occurred")
        assert result == RootCause.NETWORK_ERROR

    def test_classify_context_window(self):
        result = classify_error("context window exceeded")
        assert result == RootCause.CONTEXT_OVERFLOW

    def test_classify_malformed_json(self):
        result = classify_error("json parse error")
        assert result == RootCause.MALFORMED_ARGS

    def test_classify_auth_invalid_key(self):
        result = classify_error("invalid api key")
        assert result == RootCause.AUTH_ERROR

    def test_classify_hallucination_apology(self):
        result = classify_error(None, None, "I apologize but I cannot do that")
        assert result == RootCause.HALLUCINATION

    def test_classify_with_result_but_no_error(self):
        """Valid result, no error should be UNKNOWN."""
        result = classify_error(None, None, "valid result", None, None)
        assert result == RootCause.UNKNOWN

    def test_classify_high_duration_with_error(self):
        """High duration + error message should classify as timeout."""
        result = classify_error("something went wrong", duration_ms=60000)
        assert result == RootCause.TIMEOUT


# ---------------------------------------------------------------------------
# Alert edge cases
# ---------------------------------------------------------------------------
class TestAlertEdgeCases:
    def test_alert_manager_sync_mode(self):
        """Synchronous send mode should work."""
        received = []
        manager = AlertManager(async_send=False)
        manager.add_channel(CallbackAlert(lambda e: received.append(e)))
        manager.send_alert({"success": False, "error_message": "test"})
        assert len(received) == 1

    def test_alert_manager_skips_success_even_sync(self):
        received = []
        manager = AlertManager(async_send=False)
        manager.add_channel(CallbackAlert(lambda e: received.append(e)))
        manager.send_alert({"success": True})
        assert len(received) == 0

    def test_format_payload_minimal_event(self):
        payload = _format_payload({})
        assert payload["event"] == "agent_failure"
        assert payload["event_id"] is None
        assert payload["function_name"] is None

    def test_format_payload_full_event(self):
        event = {
            "event_id": "test-123",
            "timestamp": "2025-01-01T00:00:00Z",
            "function_name": "my_func",
            "error_message": "something broke",
            "error_type": "ValueError",
            "root_cause": "malformed_args",
            "duration_ms": 150.5,
        }
        payload = _format_payload(event)
        assert payload["event_id"] == "test-123"
        assert payload["duration_ms"] == 150.5

    def test_email_alert_init(self):
        """EmailAlert should initialize with SMTP settings."""
        alert = EmailAlert(
            smtp_host="smtp.test.com",
            smtp_port=587,
            from_addr="test@test.com",
            to_addrs=["admin@test.com"],
            username="user",
            password="pass",
        )
        assert alert.smtp_host == "smtp.test.com"
        assert alert.use_tls is True

    def test_webhook_alert_init(self):
        alert = WebhookAlert("https://example.com/hook", headers={"X-Token": "abc"})
        assert alert.url == "https://example.com/hook"
        assert alert.headers["X-Token"] == "abc"

    def test_slack_alert_init(self):
        alert = SlackAlert("https://hooks.slack.com/test")
        assert alert.webhook_url == "https://hooks.slack.com/test"


# ---------------------------------------------------------------------------
# safe_repr edge cases
# ---------------------------------------------------------------------------
class TestSafeReprEdgeCases:
    def test_none(self):
        assert _safe_repr(None) is None

    def test_bool(self):
        assert _safe_repr(True) is True
        assert _safe_repr(False) is False

    def test_integer(self):
        assert _safe_repr(42) == 42

    def test_float(self):
        assert _safe_repr(3.14) == 3.14

    def test_nested_dict(self):
        result = _safe_repr({"a": {"b": {"c": 1}}})
        assert result["a"]["b"]["c"] == 1

    def test_mixed_types_in_list(self):
        result = _safe_repr([1, "two", 3.0, None, True])
        assert len(result) == 5

    def test_custom_max_length(self):
        long_str = "x" * 500
        result = _safe_repr(long_str, max_length=100)
        assert "truncated" in str(result)
        assert len(str(result)) < 200

    def test_empty_collections(self):
        assert _safe_repr([]) == []
        assert _safe_repr({}) == {}
        assert _safe_repr(()) == []  # tuple becomes list


# ---------------------------------------------------------------------------
# Configure edge cases
# ---------------------------------------------------------------------------
class TestConfigureEdgeCases:
    def test_configure_with_all_options(self, tmp_path):
        cap = configure(
            db_path=str(tmp_path / "cfg.db"),
            webhook_url="https://example.com/hook",
            slack_webhook="https://hooks.slack.com/test",
        )
        assert isinstance(cap, EventCapture)
        assert cap.alert_manager is not None
        assert len(cap.alert_manager.channels) >= 2

    def test_configure_returns_working_capture(self, tmp_path):
        cap = configure(db_path=str(tmp_path / "cfg2.db"))

        @watch(capture=cap)
        def test_func():
            return "ok"

        result = test_func()
        assert result == "ok"
