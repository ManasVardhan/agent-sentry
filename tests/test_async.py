"""Tests for async event capture."""

import asyncio
import pytest
from agent_sentry.capture import EventCapture
from agent_sentry.storage import EventStore
from agent_sentry.alerts import AlertManager, CallbackAlert


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "test.db"))


@pytest.fixture
def capture(store):
    return EventCapture(store=store, alert_manager=AlertManager(async_send=False))


def test_async_capture_successful(capture, store):
    """async_capture_call should record successful async calls."""
    async def async_add(a, b):
        return a + b

    result = asyncio.run(
        capture.async_capture_call(async_add, (3, 4), {})
    )
    assert result == 7

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert "async_add" in events[0]["function_name"]
    assert events[0]["duration_ms"] > 0


def test_async_capture_failure(capture, store):
    """async_capture_call should record failures and re-raise."""
    async def async_fail():
        raise RuntimeError("async boom")

    with pytest.raises(RuntimeError, match="async boom"):
        asyncio.run(
            capture.async_capture_call(async_fail, (), {})
        )

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "RuntimeError"
    assert "async boom" in events[0]["error_message"]
    assert events[0]["root_cause"] is not None


def test_async_capture_with_metadata(capture, store):
    """async_capture_call should store metadata and tags."""
    async def async_noop():
        return "ok"

    asyncio.run(
        capture.async_capture_call(
            async_noop, (), {},
            metadata={"session": "abc123"},
            tags=["async", "test"],
        )
    )

    events = store.get_events()
    assert events[0]["metadata"]["session"] == "abc123"
    assert "async" in events[0]["tags"]
    assert "test" in events[0]["tags"]


def test_async_capture_timeout_classification(capture, store):
    """Async timeout errors should be classified as timeout."""
    async def async_timeout():
        raise TimeoutError("Connection timed out")

    with pytest.raises(TimeoutError):
        asyncio.run(
            capture.async_capture_call(async_timeout, (), {})
        )

    events = store.get_events()
    assert events[0]["root_cause"] == "timeout"


def test_async_capture_rate_limit_classification(capture, store):
    """Async rate limit errors should be classified as rate_limit."""
    async def async_rate_limited():
        raise Exception("429 Too many requests")

    with pytest.raises(Exception):
        asyncio.run(
            capture.async_capture_call(async_rate_limited, (), {})
        )

    events = store.get_events()
    assert events[0]["root_cause"] == "rate_limit"


def test_async_capture_silent_failure_none(capture, store):
    """Async functions returning None should be flagged as silent failures."""
    async def async_returns_none():
        return None

    result = asyncio.run(
        capture.async_capture_call(async_returns_none, (), {})
    )
    assert result is None

    events = store.get_events()
    assert events[0]["success"] is False
    assert events[0]["root_cause"] == "silent_failure"


def test_async_capture_silent_failure_empty_dict(capture, store):
    """Async functions returning empty dict should be flagged."""
    async def async_returns_empty():
        return {}

    result = asyncio.run(
        capture.async_capture_call(async_returns_empty, (), {})
    )
    assert result == {}

    events = store.get_events()
    assert events[0]["success"] is False


def test_async_capture_auto_classify_disabled(store):
    """With auto_classify=False, empty async results should not be flagged."""
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False), auto_classify=False)

    async def async_returns_none():
        return None

    result = asyncio.run(
        cap.async_capture_call(async_returns_none, (), {})
    )
    assert result is None

    events = store.get_events()
    assert events[0]["success"] is True


def test_async_capture_sends_alert_on_failure(store):
    """Alerts should fire for async failures."""
    alerts = []
    manager = AlertManager(async_send=False)
    manager.add_channel(CallbackAlert(lambda e: alerts.append(e)))
    cap = EventCapture(store=store, alert_manager=manager)

    async def async_fail():
        raise RuntimeError("async alert boom")

    with pytest.raises(RuntimeError):
        asyncio.run(
            cap.async_capture_call(async_fail, (), {})
        )

    assert len(alerts) == 1
    assert alerts[0]["error_message"] == "async alert boom"


def test_async_capture_with_kwargs(capture, store):
    """Async capture should handle keyword arguments properly."""
    async def async_greet(name, greeting="Hello"):
        return f"{greeting}, {name}!"

    result = asyncio.run(
        capture.async_capture_call(async_greet, ("World",), {"greeting": "Hi"})
    )
    assert result == "Hi, World!"

    events = store.get_events()
    assert events[0]["success"] is True
    assert events[0]["duration_ms"] >= 0


def test_async_capture_preserves_function_qualname(capture, store):
    """Async capture should preserve the qualified name of methods."""
    class MyService:
        async def process(self):
            return "done"

    svc = MyService()
    asyncio.run(
        capture.async_capture_call(svc.process, (), {})
    )

    events = store.get_events()
    assert "MyService" in events[0]["function_name"]
    assert "process" in events[0]["function_name"]


def test_async_capture_network_error_classification(capture, store):
    """Async connection errors should be classified as network_error."""
    async def async_network_fail():
        raise ConnectionRefusedError("Connection refused")

    with pytest.raises(ConnectionRefusedError):
        asyncio.run(
            capture.async_capture_call(async_network_fail, (), {})
        )

    events = store.get_events()
    assert events[0]["root_cause"] == "network_error"


def test_async_capture_auth_error_classification(capture, store):
    """Async auth errors should be classified as auth_error."""
    async def async_auth_fail():
        raise Exception("401 Unauthorized: Invalid API key")

    with pytest.raises(Exception):
        asyncio.run(
            capture.async_capture_call(async_auth_fail, (), {})
        )

    events = store.get_events()
    assert events[0]["root_cause"] == "auth_error"


def test_async_capture_context_overflow_classification(capture, store):
    """Async context overflow should be classified correctly."""
    async def async_context_fail():
        raise Exception("maximum context length exceeded, token limit reached")

    with pytest.raises(Exception):
        asyncio.run(
            capture.async_capture_call(async_context_fail, (), {})
        )

    events = store.get_events()
    assert events[0]["root_cause"] == "context_overflow"


def test_async_capture_event_type(capture, store):
    """Async capture should use the specified event_type."""
    async def async_tool():
        return "tool result"

    asyncio.run(
        capture.async_capture_call(async_tool, (), {}, event_type="tool_call")
    )

    events = store.get_events()
    assert events[0]["event_type"] == "tool_call"


def test_async_capture_traceback_on_error(capture, store):
    """Async capture should include traceback for errors."""
    async def async_error():
        raise ValueError("detailed error")

    with pytest.raises(ValueError):
        asyncio.run(
            capture.async_capture_call(async_error, (), {})
        )

    events = store.get_events()
    assert events[0]["traceback"] is not None
    assert "ValueError" in events[0]["traceback"]
    assert "detailed error" in events[0]["traceback"]
