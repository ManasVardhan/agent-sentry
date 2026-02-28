"""Tests for event capture."""

import pytest
from agent_sentry.capture import EventCapture, _safe_repr
from agent_sentry.storage import EventStore
from agent_sentry.alerts import AlertManager


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "test.db"))


@pytest.fixture
def capture(store):
    return EventCapture(store=store, alert_manager=AlertManager(async_send=False))


def test_capture_successful_call(capture, store):
    def add(a, b):
        return a + b

    result = capture.capture_call(add, (2, 3), {})
    assert result == 5

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is True
    assert "add" in events[0]["function_name"]
    assert events[0]["duration_ms"] > 0


def test_capture_failed_call(capture, store):
    def fail():
        raise ValueError("something broke")

    with pytest.raises(ValueError, match="something broke"):
        capture.capture_call(fail, (), {})

    events = store.get_events()
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["error_type"] == "ValueError"
    assert "something broke" in events[0]["error_message"]
    assert events[0]["root_cause"] is not None


def test_capture_with_metadata(capture, store):
    def noop():
        return "ok"

    capture.capture_call(noop, (), {}, metadata={"session": "abc"}, tags=["test"])
    events = store.get_events()
    assert events[0]["metadata"]["session"] == "abc"
    assert "test" in events[0]["tags"]


def test_capture_timeout_classification(capture, store):
    def timeout_func():
        raise TimeoutError("Connection timed out")

    with pytest.raises(TimeoutError):
        capture.capture_call(timeout_func, (), {})

    events = store.get_events()
    assert events[0]["root_cause"] == "timeout"


def test_capture_rate_limit_classification(capture, store):
    def rate_limited():
        raise Exception("429 Too many requests")

    with pytest.raises(Exception):
        capture.capture_call(rate_limited, (), {})

    events = store.get_events()
    assert events[0]["root_cause"] == "rate_limit"


def test_log_event_manually(capture, store):
    capture.log_event({
        "event_type": "custom",
        "function_name": "manual_log",
        "success": True,
    })
    events = store.get_events()
    assert len(events) == 1
    assert events[0]["function_name"] == "manual_log"


def test_safe_repr_primitives():
    assert _safe_repr(42) == 42
    assert _safe_repr("hello") == "hello"
    assert _safe_repr(True) is True
    assert _safe_repr(None) is None


def test_safe_repr_truncates_long_string():
    long_str = "x" * 2000
    result = _safe_repr(long_str, max_length=100)
    assert len(result) < 200
    assert "truncated" in result


def test_safe_repr_handles_dict():
    result = _safe_repr({"key": "value", "num": 42})
    assert result["key"] == "value"
    assert result["num"] == 42


def test_safe_repr_handles_list():
    result = _safe_repr([1, 2, 3])
    assert result == [1, 2, 3]
