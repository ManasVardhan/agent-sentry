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


def test_safe_repr_truncates_long_list():
    long_list = list(range(50))
    result = _safe_repr(long_list)
    # Should have 20 items + truncation marker
    assert len(result) == 21
    assert "more" in str(result[-1])


def test_safe_repr_truncates_long_dict():
    long_dict = {f"key_{i}": i for i in range(50)}
    result = _safe_repr(long_dict)
    assert "__truncated__" in result
    assert "more" in result["__truncated__"]


def test_safe_repr_unserializable():
    class Weird:
        def __str__(self):
            raise RuntimeError("can't str me")

    result = _safe_repr(Weird())
    assert "unserializable" in result


def test_capture_silent_failure_none(capture, store):
    """Functions returning None should be flagged as silent failures."""
    def returns_none():
        return None

    result = capture.capture_call(returns_none, (), {})
    assert result is None

    events = store.get_events()
    assert events[0]["success"] is False
    assert events[0]["root_cause"] == "silent_failure"


def test_capture_silent_failure_empty_list(capture, store):
    """Functions returning empty list should be flagged."""
    def returns_empty():
        return []

    result = capture.capture_call(returns_empty, (), {})
    assert result == []

    events = store.get_events()
    assert events[0]["success"] is False


def test_capture_auto_classify_disabled(store):
    """With auto_classify=False, empty results should not be flagged."""
    cap = EventCapture(store=store, alert_manager=AlertManager(async_send=False), auto_classify=False)

    def returns_none():
        return None

    result = cap.capture_call(returns_none, (), {})
    assert result is None

    events = store.get_events()
    assert events[0]["success"] is True


def test_capture_sends_alert_on_failure(store):
    """Alerts should fire for failures."""
    alerts = []
    manager = AlertManager(async_send=False)
    from agent_sentry import CallbackAlert
    manager.add_channel(CallbackAlert(lambda e: alerts.append(e)))
    cap = EventCapture(store=store, alert_manager=manager)

    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        cap.capture_call(fail, (), {})

    assert len(alerts) == 1
    assert alerts[0]["error_message"] == "boom"


def test_log_event_failed_manually(capture, store):
    """Manually logged failed events should get classified."""
    capture.log_event({
        "event_type": "manual",
        "function_name": "test",
        "success": False,
        "error_message": "Connection timed out",
    })
    events = store.get_events()
    assert events[0]["root_cause"] == "timeout"


def test_capture_preserves_function_qualname(capture, store):
    class MyClass:
        def method(self):
            return "ok"

    obj = MyClass()
    capture.capture_call(obj.method, (), {})
    events = store.get_events()
    assert "MyClass" in events[0]["function_name"]
    assert "method" in events[0]["function_name"]
