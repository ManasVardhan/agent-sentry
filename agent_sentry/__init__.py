"""agent-sentry: Crash reporting for AI agents. Catch failures before your users do."""

__version__ = "0.1.0"

import inspect
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from .capture import EventCapture, get_capture, reset_capture
from .storage import EventStore, get_store
from .alerts import (
    AlertManager,
    AlertChannel,
    WebhookAlert,
    SlackAlert,
    EmailAlert,
    CallbackAlert,
    get_alert_manager,
)
from .analysis import RootCause, classify_error


def watch(
    func: Optional[Callable] = None,
    *,
    event_type: str = "function_call",
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    capture: Optional[EventCapture] = None,
):
    """Decorator that wraps a function and captures all events.

    Can be used with or without arguments. Works with both sync and async functions:

        @watch
        def my_agent(query):
            ...

        @watch
        async def async_agent(query):
            ...

        @watch(event_type="tool_call", tags=["search"])
        def search_tool(query):
            ...

    Args:
        func: The function to wrap (when used without arguments).
        event_type: Type of event to log (function_call, llm_call, tool_call).
        tags: Tags for categorizing this function's events.
        metadata: Additional metadata to attach to events.
        capture: Custom EventCapture instance to use.
    """
    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                cap = capture or get_capture()
                return await cap.async_capture_call(
                    fn, args, kwargs,
                    event_type=event_type,
                    metadata=metadata,
                    tags=tags,
                )
            return async_wrapper
        else:
            @wraps(fn)
            def wrapper(*args, **kwargs):
                cap = capture or get_capture()
                return cap.capture_call(
                    fn, args, kwargs,
                    event_type=event_type,
                    metadata=metadata,
                    tags=tags,
                )
            return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def configure(
    db_path: Optional[str] = None,
    alert_channels: Optional[List[AlertChannel]] = None,
    webhook_url: Optional[str] = None,
    slack_webhook: Optional[str] = None,
) -> EventCapture:
    """Configure agent-sentry with custom settings.

    Args:
        db_path: Path to SQLite database file.
        alert_channels: List of AlertChannel instances.
        webhook_url: URL for webhook alerts (convenience shortcut).
        slack_webhook: Slack webhook URL (convenience shortcut).

    Returns:
        Configured EventCapture instance.
    """
    store = get_store(db_path)
    alert_manager = get_alert_manager()

    if alert_channels:
        for channel in alert_channels:
            alert_manager.add_channel(channel)

    if webhook_url:
        alert_manager.add_channel(WebhookAlert(webhook_url))

    if slack_webhook:
        alert_manager.add_channel(SlackAlert(slack_webhook))

    cap = EventCapture(store=store, alert_manager=alert_manager)

    # Set as global default
    import agent_sentry.capture as capture_mod
    capture_mod._default_capture = cap

    return cap


__all__ = [
    "watch",
    "configure",
    "EventCapture",
    "EventStore",
    "AlertManager",
    "AlertChannel",
    "WebhookAlert",
    "SlackAlert",
    "EmailAlert",
    "CallbackAlert",
    "RootCause",
    "classify_error",
    "get_store",
    "get_capture",
    "reset_capture",
    "get_alert_manager",
    "__version__",
]
