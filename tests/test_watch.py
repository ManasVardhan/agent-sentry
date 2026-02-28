"""Tests for the @watch decorator."""

import pytest
from agent_sentry import watch, EventCapture
from agent_sentry.storage import EventStore
from agent_sentry.alerts import AlertManager
from agent_sentry.capture import reset_capture
import agent_sentry.capture as capture_mod


@pytest.fixture(autouse=True)
def setup_capture(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False))
    capture_mod._default_capture = cap
    yield store, cap
    reset_capture()


def test_watch_simple(setup_capture):
    store, cap = setup_capture

    @watch
    def greet(name):
        return f"Hello, {name}!"

    result = greet("World")
    assert result == "Hello, World!"

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert "greet" in events[0]["function_name"]


def test_watch_with_args(setup_capture):
    store, cap = setup_capture

    @watch(event_type="tool_call", tags=["search"])
    def search(query):
        return [f"result for {query}"]

    result = search("python")
    assert result == ["result for python"]

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"
    assert "search" in events[0]["tags"]


def test_watch_captures_exception(setup_capture):
    store, cap = setup_capture

    @watch
    def broken():
        raise RuntimeError("oops")

    with pytest.raises(RuntimeError, match="oops"):
        broken()

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "RuntimeError"


def test_watch_preserves_function_name(setup_capture):
    @watch
    def my_special_function():
        """My docstring."""
        return 42

    assert my_special_function.__name__ == "my_special_function"
    assert my_special_function.__doc__ == "My docstring."


def test_watch_with_custom_capture(tmp_path):
    store = EventStore(str(tmp_path / "custom.db"))
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False))

    @watch(capture=cap)
    def custom():
        return "custom"

    custom()
    events = store.get_events()
    assert len(events) == 1
