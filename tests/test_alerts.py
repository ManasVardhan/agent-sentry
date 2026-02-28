"""Tests for the alert system."""

import pytest
from agent_sentry.alerts import (
    AlertManager,
    CallbackAlert,
    WebhookAlert,
    SlackAlert,
    _format_payload,
)


@pytest.fixture
def failure_event():
    return {
        "event_id": "test-123",
        "timestamp": "2026-02-28T00:00:00+00:00",
        "event_type": "function_call",
        "function_name": "broken_func",
        "success": False,
        "error_message": "Something failed",
        "error_type": "RuntimeError",
        "root_cause": "unknown",
        "duration_ms": 250.0,
    }


def test_callback_alert(failure_event):
    received = []

    def on_alert(event):
        received.append(event)

    manager = AlertManager(
        channels=[CallbackAlert(on_alert)],
        async_send=False,
    )
    manager.send_alert(failure_event)
    assert len(received) == 1
    assert received[0]["event_id"] == "test-123"


def test_alert_skips_success():
    received = []

    manager = AlertManager(
        channels=[CallbackAlert(lambda e: received.append(e))],
        async_send=False,
    )
    manager.send_alert({"success": True, "event_id": "ok"})
    assert len(received) == 0


def test_multiple_channels(failure_event):
    r1, r2 = [], []

    manager = AlertManager(
        channels=[
            CallbackAlert(lambda e: r1.append(e)),
            CallbackAlert(lambda e: r2.append(e)),
        ],
        async_send=False,
    )
    manager.send_alert(failure_event)
    assert len(r1) == 1
    assert len(r2) == 1


def test_alert_channel_error_doesnt_break(failure_event):
    received = []

    def bad_callback(e):
        raise RuntimeError("alert failed")

    manager = AlertManager(
        channels=[
            CallbackAlert(bad_callback),
            CallbackAlert(lambda e: received.append(e)),
        ],
        async_send=False,
    )
    manager.send_alert(failure_event)
    # Second channel still receives the alert
    assert len(received) == 1


def test_format_payload(failure_event):
    payload = _format_payload(failure_event)
    assert payload["event"] == "agent_failure"
    assert payload["function_name"] == "broken_func"
    assert payload["root_cause"] == "unknown"


def test_add_channel():
    manager = AlertManager()
    assert len(manager.channels) == 0
    manager.add_channel(CallbackAlert(lambda e: None))
    assert len(manager.channels) == 1
