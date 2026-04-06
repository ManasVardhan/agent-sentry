"""Extended tests for the alert system: WebhookAlert retries, SlackAlert, EmailAlert."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from agent_sentry.alerts import (
    AlertManager,
    EmailAlert,
    SlackAlert,
    WebhookAlert,
    _format_payload,
    get_alert_manager,
)


@pytest.fixture
def failure_event():
    return {
        "event_id": "evt-1",
        "timestamp": "2026-04-05T00:00:00+00:00",
        "event_type": "function_call",
        "function_name": "broken_func",
        "success": False,
        "error_message": "boom",
        "error_type": "RuntimeError",
        "root_cause": "rate_limit",
        "duration_ms": 320.0,
        "traceback": "Traceback (most recent call last):\n  File 'x.py', line 1\nRuntimeError: boom",
    }


# WebhookAlert ----------------------------------------------------------------


class _MockResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_webhook_alert_success(failure_event):
    alert = WebhookAlert("https://example.com/hook")
    with patch("agent_sentry.alerts.urlopen", return_value=_MockResponse(200)) as mock_open:
        ok = alert.send(failure_event)
    assert ok is True
    assert mock_open.call_count == 1


def test_webhook_alert_4xx_no_retry(failure_event):
    alert = WebhookAlert("https://example.com/hook", max_retries=3, base_delay=0.0)
    with patch("agent_sentry.alerts.urlopen", return_value=_MockResponse(401)) as mock_open:
        ok = alert.send(failure_event)
    assert ok is False
    assert mock_open.call_count == 1


def test_webhook_alert_5xx_retries_then_success(failure_event):
    alert = WebhookAlert("https://example.com/hook", max_retries=3, base_delay=0.0)
    responses = [_MockResponse(503), _MockResponse(503), _MockResponse(200)]
    call_log = iter(responses)
    with patch("agent_sentry.alerts.urlopen", side_effect=lambda *a, **k: next(call_log)) as mock_open:
        with patch("agent_sentry.alerts.time.sleep"):
            ok = alert.send(failure_event)
    assert ok is True
    assert mock_open.call_count == 3


def test_webhook_alert_5xx_exhausts_retries(failure_event):
    alert = WebhookAlert("https://example.com/hook", max_retries=2, base_delay=0.0)
    with patch("agent_sentry.alerts.urlopen", return_value=_MockResponse(500)) as mock_open:
        with patch("agent_sentry.alerts.time.sleep"):
            ok = alert.send(failure_event)
    assert ok is False
    # Final attempt returns False directly (no retry); but the 5xx-retry branch
    # only retries while attempt < max_retries, so total attempts = max_retries + 1.
    assert mock_open.call_count == alert.max_retries + 1


def test_webhook_alert_network_error_retries(failure_event):
    alert = WebhookAlert("https://example.com/hook", max_retries=2, base_delay=0.0)
    err = URLError("network down")
    with patch("agent_sentry.alerts.urlopen", side_effect=[err, err, _MockResponse(200)]) as mock_open:
        with patch("agent_sentry.alerts.time.sleep"):
            ok = alert.send(failure_event)
    assert ok is True
    assert mock_open.call_count == 3


def test_webhook_alert_network_error_exhausts(failure_event):
    alert = WebhookAlert("https://example.com/hook", max_retries=1, base_delay=0.0)
    with patch("agent_sentry.alerts.urlopen", side_effect=URLError("dead")) as mock_open:
        with patch("agent_sentry.alerts.time.sleep"):
            ok = alert.send(failure_event)
    assert ok is False
    assert mock_open.call_count == 2


def test_webhook_alert_includes_extra_headers(failure_event):
    alert = WebhookAlert(
        "https://example.com/hook",
        headers={"Authorization": "Bearer token123"},
    )
    captured = {}

    def fake_open(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["data"] = req.data
        return _MockResponse(200)

    with patch("agent_sentry.alerts.urlopen", side_effect=fake_open):
        alert.send(failure_event)

    # urllib lower-cases header names but preserves first-letter capitalization
    assert any("Authorization" == k for k in captured["headers"]) or any(
        k.lower() == "authorization" for k in captured["headers"]
    )
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["event"] == "agent_failure"
    assert payload["function_name"] == "broken_func"


# SlackAlert ------------------------------------------------------------------


def test_slack_alert_success(failure_event):
    alert = SlackAlert("https://hooks.slack.com/services/T0/B0/XXX")
    captured = {}

    def fake_open(req, timeout=None):
        captured["data"] = req.data
        return _MockResponse(200)

    with patch("agent_sentry.alerts.urlopen", side_effect=fake_open):
        ok = alert.send(failure_event)
    assert ok is True
    payload = json.loads(captured["data"].decode("utf-8"))
    assert "blocks" in payload
    assert any(":rotating_light:" in str(b) for b in payload["blocks"])


def test_slack_alert_failure_returns_false(failure_event):
    alert = SlackAlert("https://hooks.slack.com/services/T0/B0/XXX")
    with patch("agent_sentry.alerts.urlopen", side_effect=URLError("nope")):
        ok = alert.send(failure_event)
    assert ok is False


def test_slack_alert_truncates_long_error_message():
    alert = SlackAlert("https://hooks.slack.com/services/T0/B0/XXX")
    captured = {}

    def fake_open(req, timeout=None):
        captured["data"] = req.data
        return _MockResponse(200)

    long_msg = "x" * 1000
    event = {
        "function_name": "f",
        "error_message": long_msg,
        "root_cause": "unknown",
        "duration_ms": 0,
        "success": False,
    }
    with patch("agent_sentry.alerts.urlopen", side_effect=fake_open):
        alert.send(event)
    payload = json.loads(captured["data"].decode("utf-8"))
    fields_text = json.dumps(payload["blocks"])
    # Truncated to 200 chars
    assert "x" * 200 in fields_text
    assert "x" * 250 not in fields_text


def test_slack_alert_handles_missing_fields():
    alert = SlackAlert("https://hooks.slack.com/services/T0/B0/XXX")
    with patch("agent_sentry.alerts.urlopen", return_value=_MockResponse(200)):
        ok = alert.send({"success": False})
    assert ok is True


# EmailAlert ------------------------------------------------------------------


def test_email_alert_success_with_tls_and_auth(failure_event):
    alert = EmailAlert(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_addr="bot@example.com",
        to_addrs=["alice@example.com", "bob@example.com"],
        username="bot",
        password="hunter2",
        use_tls=True,
    )
    fake_smtp = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_smtp
    cm.__exit__.return_value = False
    with patch("agent_sentry.alerts.smtplib.SMTP", return_value=cm) as mock_smtp:
        ok = alert.send(failure_event)
    assert ok is True
    mock_smtp.assert_called_once_with("smtp.example.com", 587)
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("bot", "hunter2")
    fake_smtp.sendmail.assert_called_once()
    args = fake_smtp.sendmail.call_args[0]
    assert args[0] == "bot@example.com"
    assert args[1] == ["alice@example.com", "bob@example.com"]
    body = args[2]
    assert "broken_func" in body
    assert "rate_limit" in body
    # Traceback included when present
    assert "Traceback" in body


def test_email_alert_no_tls_no_auth(failure_event):
    alert = EmailAlert(
        smtp_host="smtp.example.com",
        smtp_port=25,
        from_addr="bot@example.com",
        to_addrs=["alice@example.com"],
        use_tls=False,
    )
    fake_smtp = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_smtp
    cm.__exit__.return_value = False
    with patch("agent_sentry.alerts.smtplib.SMTP", return_value=cm):
        ok = alert.send(failure_event)
    assert ok is True
    fake_smtp.starttls.assert_not_called()
    fake_smtp.login.assert_not_called()


def test_email_alert_smtp_failure(failure_event):
    alert = EmailAlert(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_addr="bot@example.com",
        to_addrs=["alice@example.com"],
    )
    with patch("agent_sentry.alerts.smtplib.SMTP", side_effect=ConnectionError("dead")):
        ok = alert.send(failure_event)
    assert ok is False


def test_email_alert_without_traceback():
    alert = EmailAlert(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_addr="bot@example.com",
        to_addrs=["alice@example.com"],
    )
    fake_smtp = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_smtp
    cm.__exit__.return_value = False
    event = {
        "function_name": "f",
        "error_message": "boom",
        "root_cause": "unknown",
        "duration_ms": 100,
        "timestamp": "2026-04-05",
        "event_id": "abc",
    }
    with patch("agent_sentry.alerts.smtplib.SMTP", return_value=cm):
        ok = alert.send(event)
    assert ok is True
    body = fake_smtp.sendmail.call_args[0][2]
    assert "Traceback" not in body


# AlertManager async path -----------------------------------------------------


def test_alert_manager_async_send_runs_in_thread(failure_event):
    received = []

    class _TrackingChannel:
        def send(self, event):
            received.append(event)
            return True

    manager = AlertManager(channels=[_TrackingChannel()], async_send=True)
    manager.send_alert(failure_event)
    # Allow the daemon thread a moment to flush.
    import time as _t
    for _ in range(20):
        if received:
            break
        _t.sleep(0.01)
    assert len(received) == 1


def test_get_alert_manager_singleton():
    m1 = get_alert_manager()
    m2 = get_alert_manager()
    assert m1 is m2


def test_format_payload_with_minimal_event():
    payload = _format_payload({"event_id": "x"})
    assert payload["event"] == "agent_failure"
    assert payload["event_id"] == "x"
    assert payload["function_name"] is None
