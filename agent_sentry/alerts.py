"""Alert system for agent-sentry. Supports webhooks, Slack, and email."""

import json
import logging
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


class AlertChannel:
    """Base class for alert channels."""

    def send(self, event: Dict[str, Any]) -> bool:
        """Send an alert for a failed event. Returns True on success."""
        raise NotImplementedError


class WebhookAlert(AlertChannel):
    """Send alerts to a webhook URL (generic or Slack-compatible).

    Supports automatic retry with exponential backoff on transient failures.

    Args:
        url: The webhook endpoint URL.
        headers: Optional extra HTTP headers.
        max_retries: Maximum number of retry attempts (default 3, 0 disables retries).
        base_delay: Base delay in seconds for exponential backoff (default 1.0).
        timeout: HTTP request timeout in seconds (default 10).
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: int = 10,
    ):
        self.url = url
        self.headers = headers or {}
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.timeout = timeout

    def send(self, event: Dict[str, Any]) -> bool:
        payload = _format_payload(event)
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        headers.update(self.headers)

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            req = Request(self.url, data=data, headers=headers, method="POST")
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    if resp.status < 400:
                        return True
                    # Server error (5xx) is retriable
                    if resp.status >= 500 and attempt < self.max_retries:
                        delay = self.base_delay * (2 ** attempt)
                        logger.debug(
                            "Webhook returned %d, retrying in %.1fs (attempt %d/%d)",
                            resp.status, delay, attempt + 1, self.max_retries,
                        )
                        time.sleep(delay)
                        continue
                    return False
            except (URLError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.debug(
                        "Webhook request failed: %s, retrying in %.1fs (attempt %d/%d)",
                        exc, delay, attempt + 1, self.max_retries,
                    )
                    time.sleep(delay)
                    continue

        logger.warning("Webhook alert failed after %d attempts: %s", self.max_retries + 1, last_error)
        return False


class SlackAlert(AlertChannel):
    """Send alerts to a Slack webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, event: Dict[str, Any]) -> bool:
        func_name = event.get("function_name", "unknown")
        error_msg = event.get("error_message", "No error message")
        root_cause = event.get("root_cause", "unknown")
        duration = event.get("duration_ms", 0)

        payload = {
            "text": ":rotating_light: Agent Failure Detected",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": ":rotating_light: Agent Failure Detected",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Function:*\n`{func_name}`"},
                        {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause}"},
                        {"type": "mrkdwn", "text": f"*Error:*\n{error_msg[:200]}"},
                        {"type": "mrkdwn", "text": f"*Duration:*\n{duration:.0f}ms"},
                    ],
                },
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except (URLError, OSError):
            return False


class EmailAlert(AlertChannel):
    """Send alerts via email (SMTP)."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        from_addr: str,
        to_addrs: List[str],
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: bool = True,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.username = username
        self.password = password
        self.use_tls = use_tls

    def send(self, event: Dict[str, Any]) -> bool:
        func_name = event.get("function_name", "unknown")
        error_msg = event.get("error_message", "No error message")
        root_cause = event.get("root_cause", "unknown")

        subject = f"[agent-sentry] Failure in {func_name}: {root_cause}"
        body = (
            f"Agent Failure Report\n"
            f"{'=' * 40}\n\n"
            f"Function: {func_name}\n"
            f"Root Cause: {root_cause}\n"
            f"Error: {error_msg}\n"
            f"Duration: {event.get('duration_ms', 0):.0f}ms\n"
            f"Timestamp: {event.get('timestamp', 'unknown')}\n"
            f"Event ID: {event.get('event_id', 'unknown')}\n"
        )
        if event.get("traceback"):
            body += f"\nTraceback:\n{event['traceback']}\n"

        msg = MIMEMultipart()
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            return True
        except Exception:
            return False


def _post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
) -> bool:
    """POST a JSON payload and return True on a 2xx/3xx response."""
    data = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = Request(url, data=data, headers=all_headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status < 400
    except (URLError, OSError):
        return False


class PagerDutyAlert(AlertChannel):
    """Send alerts to PagerDuty via the Events API v2.

    Failures are triggered as PagerDuty events with the function name
    and root cause in the summary and the full event details attached.
    Events for the same function and root cause share a dedup key so
    repeated failures update one incident instead of paging repeatedly.

    Args:
        routing_key: The integration (routing) key from a PagerDuty
            Events API v2 integration.
        severity: PagerDuty severity: critical, error, warning, or info
            (default error).
        source: Value for the event source field (default agent-sentry).
        api_url: Override for the Events API endpoint.
        timeout: HTTP request timeout in seconds (default 10).
    """

    DEFAULT_API_URL = "https://events.pagerduty.com/v2/enqueue"
    _SEVERITIES = ("critical", "error", "warning", "info")

    def __init__(
        self,
        routing_key: str,
        severity: str = "error",
        source: str = "agent-sentry",
        api_url: Optional[str] = None,
        timeout: int = 10,
    ):
        if severity not in self._SEVERITIES:
            raise ValueError(
                f"severity must be one of {', '.join(self._SEVERITIES)}, "
                f"got '{severity}'"
            )
        self.routing_key = routing_key
        self.severity = severity
        self.source = source
        self.api_url = api_url or self.DEFAULT_API_URL
        self.timeout = timeout

    def build_payload(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Build the Events API v2 payload for a failure event."""
        func_name = event.get("function_name", "unknown")
        root_cause = event.get("root_cause", "unknown")
        error_msg = event.get("error_message") or "No error message"

        payload: Dict[str, Any] = {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": f"agent-sentry/{func_name}/{root_cause}",
            "payload": {
                "summary": f"[agent-sentry] {func_name} failed: {root_cause}: {error_msg}"[:1024],
                "source": self.source,
                "severity": self.severity,
                "custom_details": _format_payload(event),
            },
        }
        timestamp = event.get("timestamp")
        if timestamp:
            payload["payload"]["timestamp"] = timestamp
        return payload

    def send(self, event: Dict[str, Any]) -> bool:
        return _post_json(
            self.api_url, self.build_payload(event), timeout=self.timeout
        )


class OpsgenieAlert(AlertChannel):
    """Send alerts to Opsgenie via the Alert API.

    Failures are created as Opsgenie alerts with the function name and
    root cause in the message, the error and traceback in the
    description, and the structured event attached as details. Alerts
    for the same function and root cause share an alias so repeated
    failures deduplicate into one alert.

    Args:
        api_key: An Opsgenie API key from an API integration.
        priority: Opsgenie priority P1 through P5 (default P3).
        tags: Optional tags to attach to created alerts.
        eu: Use the EU service region endpoint (default False).
        api_url: Override for the Alert API endpoint (takes precedence
            over eu).
        timeout: HTTP request timeout in seconds (default 10).
    """

    DEFAULT_API_URL = "https://api.opsgenie.com/v2/alerts"
    EU_API_URL = "https://api.eu.opsgenie.com/v2/alerts"
    _PRIORITIES = ("P1", "P2", "P3", "P4", "P5")

    def __init__(
        self,
        api_key: str,
        priority: str = "P3",
        tags: Optional[List[str]] = None,
        eu: bool = False,
        api_url: Optional[str] = None,
        timeout: int = 10,
    ):
        if priority not in self._PRIORITIES:
            raise ValueError(
                f"priority must be one of {', '.join(self._PRIORITIES)}, "
                f"got '{priority}'"
            )
        self.api_key = api_key
        self.priority = priority
        self.tags = tags or ["agent-sentry"]
        self.api_url = api_url or (self.EU_API_URL if eu else self.DEFAULT_API_URL)
        self.timeout = timeout

    def build_payload(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Build the Alert API payload for a failure event."""
        func_name = event.get("function_name", "unknown")
        root_cause = event.get("root_cause", "unknown")
        error_msg = event.get("error_message") or "No error message"

        description = (
            f"Function: {func_name}\n"
            f"Root Cause: {root_cause}\n"
            f"Error: {error_msg}\n"
            f"Duration: {event.get('duration_ms', 0):.0f}ms\n"
            f"Timestamp: {event.get('timestamp', 'unknown')}\n"
            f"Event ID: {event.get('event_id', 'unknown')}\n"
        )
        if event.get("traceback"):
            description += f"\nTraceback:\n{event['traceback']}\n"

        details = {
            key: "" if value is None else str(value)
            for key, value in _format_payload(event).items()
        }
        return {
            "message": f"[agent-sentry] {func_name} failed: {root_cause}"[:130],
            "alias": f"agent-sentry/{func_name}/{root_cause}",
            "description": description[:15000],
            "priority": self.priority,
            "tags": self.tags,
            "details": details,
            "source": "agent-sentry",
        }

    def send(self, event: Dict[str, Any]) -> bool:
        return _post_json(
            self.api_url,
            self.build_payload(event),
            headers={"Authorization": f"GenieKey {self.api_key}"},
            timeout=self.timeout,
        )


class CallbackAlert(AlertChannel):
    """Send alerts to a custom callback function."""

    def __init__(self, callback: Callable[[Dict[str, Any]], None]):
        self.callback = callback

    def send(self, event: Dict[str, Any]) -> bool:
        try:
            self.callback(event)
            return True
        except Exception:
            return False


class AlertManager:
    """Manages multiple alert channels and sends alerts on failure."""

    def __init__(self, channels: Optional[List[AlertChannel]] = None, async_send: bool = True):
        self.channels: List[AlertChannel] = channels or []
        self.async_send = async_send

    def add_channel(self, channel: AlertChannel) -> None:
        """Add an alert channel."""
        self.channels.append(channel)

    def send_alert(self, event: Dict[str, Any]) -> None:
        """Send alert to all channels. Skips successful events."""
        if event.get("success", True):
            return

        if self.async_send:
            thread = threading.Thread(target=self._send_all, args=(event,), daemon=True)
            thread.start()
        else:
            self._send_all(event)

    def _send_all(self, event: Dict[str, Any]) -> None:
        for channel in self.channels:
            try:
                channel.send(event)
            except Exception:
                pass


def _format_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """Format an event into a webhook payload."""
    return {
        "event": "agent_failure",
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "function_name": event.get("function_name"),
        "error_message": event.get("error_message"),
        "error_type": event.get("error_type"),
        "root_cause": event.get("root_cause"),
        "duration_ms": event.get("duration_ms"),
    }


# Global alert manager
_default_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get or create the default alert manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = AlertManager()
    return _default_manager


def reset_alert_manager() -> None:
    """Reset the default alert manager."""
    global _default_manager
    _default_manager = None
