"""Tests for the top-level configure() function and reset utilities."""

import agent_sentry
from agent_sentry import (
    configure,
    CallbackAlert,
    WebhookAlert,
    SlackAlert,
    get_capture,
    reset_capture,
)
from agent_sentry.alerts import reset_alert_manager


def setup_function():
    """Reset global state between tests."""
    reset_capture()
    reset_alert_manager()


def test_configure_returns_capture(tmp_path):
    cap = configure(db_path=str(tmp_path / "test.db"))
    assert cap is not None
    assert hasattr(cap, "capture_call")
    assert hasattr(cap, "log_event")


def test_configure_sets_db_path(tmp_path):
    db = str(tmp_path / "custom.db")
    cap = configure(db_path=db)
    assert cap.store.db_path == db


def test_configure_with_webhook_url(tmp_path):
    cap = configure(
        db_path=str(tmp_path / "test.db"),
        webhook_url="https://example.com/webhook",
    )
    channels = cap.alert_manager.channels
    assert len(channels) == 1
    assert isinstance(channels[0], WebhookAlert)
    assert channels[0].url == "https://example.com/webhook"


def test_configure_with_slack_webhook(tmp_path):
    cap = configure(
        db_path=str(tmp_path / "test.db"),
        slack_webhook="https://hooks.slack.com/services/T/B/X",
    )
    channels = cap.alert_manager.channels
    assert len(channels) == 1
    assert isinstance(channels[0], SlackAlert)


def test_configure_with_custom_channels(tmp_path):
    events = []
    channel = CallbackAlert(lambda e: events.append(e))
    cap = configure(
        db_path=str(tmp_path / "test.db"),
        alert_channels=[channel],
    )
    channels = cap.alert_manager.channels
    assert len(channels) == 1
    assert channels[0] is channel


def test_configure_with_multiple_channels(tmp_path):
    events = []
    cap = configure(
        db_path=str(tmp_path / "test.db"),
        webhook_url="https://example.com/hook",
        slack_webhook="https://hooks.slack.com/services/T/B/X",
        alert_channels=[CallbackAlert(lambda e: events.append(e))],
    )
    # 1 callback + 1 webhook + 1 slack = 3
    assert len(cap.alert_manager.channels) == 3


def test_reset_capture_clears_global():
    # Accessing get_capture creates a default
    cap1 = get_capture()
    reset_capture()
    cap2 = get_capture()
    assert cap1 is not cap2


def test_version():
    assert agent_sentry.__version__ == "0.1.0"
