"""Tests for async @watch decorator support."""

import asyncio

import pytest
from agent_sentry import watch, EventCapture
from agent_sentry.storage import EventStore
from agent_sentry.alerts import AlertManager, CallbackAlert
from agent_sentry.capture import reset_capture
import agent_sentry.capture as capture_mod


@pytest.fixture(autouse=True)
def setup_capture(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False))
    capture_mod._default_capture = cap
    yield store, cap
    reset_capture()


def test_async_watch_simple(setup_capture):
    """Async functions should be properly wrapped and captured."""
    store, cap = setup_capture

    @watch
    async def async_greet(name):
        return f"Hello, {name}!"

    result = asyncio.run(async_greet("World"))
    assert result == "Hello, World!"

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert "async_greet" in events[0]["function_name"]


def test_async_watch_with_args(setup_capture):
    """Async watch with event_type and tags should work."""
    store, cap = setup_capture

    @watch(event_type="tool_call", tags=["async", "search"])
    async def async_search(query):
        return [f"result for {query}"]

    result = asyncio.run(async_search("python"))
    assert result == ["result for python"]

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"
    assert "async" in events[0]["tags"]
    assert "search" in events[0]["tags"]


def test_async_watch_captures_exception(setup_capture):
    """Async watch should capture and re-raise exceptions."""
    store, cap = setup_capture

    @watch
    async def async_broken():
        raise RuntimeError("async oops")

    with pytest.raises(RuntimeError, match="async oops"):
        asyncio.run(async_broken())

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "RuntimeError"
    assert "async oops" in events[0]["error_message"]
    assert events[0]["root_cause"] is not None


def test_async_watch_preserves_function_name(setup_capture):
    """Async watch should preserve function name and docstring."""

    @watch
    async def my_async_function():
        """My async docstring."""
        return 42

    assert my_async_function.__name__ == "my_async_function"
    assert my_async_function.__doc__ == "My async docstring."


def test_async_watch_with_custom_capture(tmp_path):
    """Async watch should accept a custom capture instance."""
    store = EventStore(str(tmp_path / "custom.db"))
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False))

    @watch(capture=cap)
    async def custom_async():
        return "custom async"

    result = asyncio.run(custom_async())
    assert result == "custom async"

    events = store.get_events()
    assert len(events) == 1


def test_async_watch_sends_alert_on_failure(tmp_path):
    """Async watch should fire alerts for failures."""
    store = EventStore(str(tmp_path / "alert.db"))
    alerts = []
    manager = AlertManager(async_send=False)
    manager.add_channel(CallbackAlert(lambda e: alerts.append(e)))
    cap = EventCapture(store=store, alert_manager=manager)

    @watch(capture=cap)
    async def async_fail():
        raise ValueError("async boom")

    with pytest.raises(ValueError, match="async boom"):
        asyncio.run(async_fail())

    assert len(alerts) == 1
    assert alerts[0]["error_message"] == "async boom"


def test_async_watch_classifies_root_cause(setup_capture):
    """Async watch should classify root causes for errors."""
    store, cap = setup_capture

    @watch
    async def async_timeout():
        raise TimeoutError("Connection timed out")

    with pytest.raises(TimeoutError):
        asyncio.run(async_timeout())

    events = store.get_events()
    assert events[0]["root_cause"] == "timeout"


def test_async_watch_records_duration(setup_capture):
    """Async watch should record duration in milliseconds."""
    store, cap = setup_capture

    @watch
    async def async_slow():
        await asyncio.sleep(0.05)
        return "done"

    asyncio.run(async_slow())

    events = store.get_events()
    assert events[0]["duration_ms"] >= 40  # at least 40ms


def test_async_watch_silent_failure_detection(setup_capture):
    """Async watch should detect silent failures (None return)."""
    store, cap = setup_capture

    @watch
    async def async_returns_none():
        return None

    result = asyncio.run(async_returns_none())
    assert result is None

    events = store.get_events()
    assert events[0]["success"] is False
    assert events[0]["root_cause"] == "silent_failure"


def test_async_and_sync_coexist(setup_capture):
    """Sync and async watched functions should both work independently."""
    store, cap = setup_capture

    @watch
    def sync_func():
        return "sync"

    @watch
    async def async_func():
        return "async"

    sync_result = sync_func()
    async_result = asyncio.run(async_func())

    assert sync_result == "sync"
    assert async_result == "async"

    events = store.get_events()
    assert len(events) == 2


def test_async_watch_with_metadata(setup_capture):
    """Async watch with metadata should pass metadata to the event."""
    store, cap = setup_capture

    @watch(metadata={"env": "test", "version": "1.0"})
    async def async_with_meta():
        return "ok"

    asyncio.run(async_with_meta())

    events = store.get_events()
    assert events[0]["metadata"]["env"] == "test"
    assert events[0]["metadata"]["version"] == "1.0"


def test_async_watch_is_coroutine_function(setup_capture):
    """Wrapped async functions should still be recognized as coroutine functions."""
    import inspect

    @watch
    async def async_func():
        return 42

    assert inspect.iscoroutinefunction(async_func)


def test_sync_watch_is_not_coroutine_function(setup_capture):
    """Wrapped sync functions should NOT be recognized as coroutine functions."""
    import inspect

    @watch
    def sync_func():
        return 42

    assert not inspect.iscoroutinefunction(sync_func)
