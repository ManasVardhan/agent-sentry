"""Tests for the PagerDuty and Opsgenie alert channels."""

import json
from unittest import mock

import pytest

from agent_sentry.alerts import OpsgenieAlert, PagerDutyAlert


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
        "root_cause": "timeout",
        "duration_ms": 250.0,
    }


def _mock_response(status=202):
    resp = mock.MagicMock()
    resp.status = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestPagerDutyAlert:
    def test_payload_structure(self, failure_event):
        channel = PagerDutyAlert("rk-123")
        payload = channel.build_payload(failure_event)
        assert payload["routing_key"] == "rk-123"
        assert payload["event_action"] == "trigger"
        assert payload["dedup_key"] == "agent-sentry/broken_func/timeout"
        inner = payload["payload"]
        assert "broken_func" in inner["summary"]
        assert "timeout" in inner["summary"]
        assert inner["severity"] == "error"
        assert inner["source"] == "agent-sentry"
        assert inner["timestamp"] == failure_event["timestamp"]
        assert inner["custom_details"]["event_id"] == "test-123"

    def test_payload_without_timestamp(self, failure_event):
        failure_event.pop("timestamp")
        payload = PagerDutyAlert("rk").build_payload(failure_event)
        assert "timestamp" not in payload["payload"]

    def test_custom_severity_and_source(self, failure_event):
        channel = PagerDutyAlert("rk", severity="critical", source="my-agent")
        inner = channel.build_payload(failure_event)["payload"]
        assert inner["severity"] == "critical"
        assert inner["source"] == "my-agent"

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError, match="severity"):
            PagerDutyAlert("rk", severity="fatal")

    def test_send_posts_to_events_api(self, failure_event):
        channel = PagerDutyAlert("rk-123")
        with mock.patch(
            "agent_sentry.alerts.urlopen", return_value=_mock_response()
        ) as urlopen:
            assert channel.send(failure_event) is True
        req = urlopen.call_args[0][0]
        assert req.full_url == PagerDutyAlert.DEFAULT_API_URL
        body = json.loads(req.data.decode("utf-8"))
        assert body["routing_key"] == "rk-123"

    def test_send_returns_false_on_http_error(self, failure_event):
        channel = PagerDutyAlert("rk-123")
        with mock.patch(
            "agent_sentry.alerts.urlopen", return_value=_mock_response(status=400)
        ):
            assert channel.send(failure_event) is False

    def test_send_returns_false_on_network_error(self, failure_event):
        channel = PagerDutyAlert("rk-123")
        with mock.patch(
            "agent_sentry.alerts.urlopen", side_effect=OSError("boom")
        ):
            assert channel.send(failure_event) is False


class TestOpsgenieAlert:
    def test_payload_structure(self, failure_event):
        channel = OpsgenieAlert("og-key")
        payload = channel.build_payload(failure_event)
        assert payload["message"] == "[agent-sentry] broken_func failed: timeout"
        assert payload["alias"] == "agent-sentry/broken_func/timeout"
        assert payload["priority"] == "P3"
        assert payload["tags"] == ["agent-sentry"]
        assert "Something failed" in payload["description"]
        assert payload["details"]["event_id"] == "test-123"
        assert all(isinstance(v, str) for v in payload["details"].values())

    def test_traceback_included_in_description(self, failure_event):
        failure_event["traceback"] = "Traceback: line 1"
        payload = OpsgenieAlert("og-key").build_payload(failure_event)
        assert "Traceback: line 1" in payload["description"]

    def test_custom_priority_and_tags(self, failure_event):
        channel = OpsgenieAlert("og-key", priority="P1", tags=["prod", "agents"])
        payload = channel.build_payload(failure_event)
        assert payload["priority"] == "P1"
        assert payload["tags"] == ["prod", "agents"]

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValueError, match="priority"):
            OpsgenieAlert("og-key", priority="P9")

    def test_eu_endpoint(self):
        assert OpsgenieAlert("k", eu=True).api_url == OpsgenieAlert.EU_API_URL
        assert OpsgenieAlert("k").api_url == OpsgenieAlert.DEFAULT_API_URL
        assert OpsgenieAlert("k", api_url="http://x").api_url == "http://x"

    def test_send_posts_with_geniekey_header(self, failure_event):
        channel = OpsgenieAlert("og-key")
        with mock.patch(
            "agent_sentry.alerts.urlopen", return_value=_mock_response()
        ) as urlopen:
            assert channel.send(failure_event) is True
        req = urlopen.call_args[0][0]
        assert req.full_url == OpsgenieAlert.DEFAULT_API_URL
        assert req.get_header("Authorization") == "GenieKey og-key"
        body = json.loads(req.data.decode("utf-8"))
        assert body["alias"] == "agent-sentry/broken_func/timeout"

    def test_send_returns_false_on_network_error(self, failure_event):
        channel = OpsgenieAlert("og-key")
        with mock.patch(
            "agent_sentry.alerts.urlopen", side_effect=OSError("boom")
        ):
            assert channel.send(failure_event) is False


class TestConfigureShortcuts:
    def test_configure_adds_channels(self, tmp_path):
        import agent_sentry
        from agent_sentry.alerts import reset_alert_manager

        reset_alert_manager()
        try:
            cap = agent_sentry.configure(
                db_path=str(tmp_path / "events.db"),
                pagerduty_routing_key="rk-123",
                opsgenie_api_key="og-key",
            )
            types = [type(c) for c in cap.alert_manager.channels]
            assert PagerDutyAlert in types
            assert OpsgenieAlert in types
        finally:
            reset_alert_manager()
            agent_sentry.reset_capture()

    def test_exports(self):
        import agent_sentry

        assert agent_sentry.PagerDutyAlert is PagerDutyAlert
        assert agent_sentry.OpsgenieAlert is OpsgenieAlert
        assert "PagerDutyAlert" in agent_sentry.__all__
        assert "OpsgenieAlert" in agent_sentry.__all__
