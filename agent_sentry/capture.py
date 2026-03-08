"""Event capture for agent-sentry. Intercepts and logs agent events."""

import time
import uuid
import traceback as tb
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .analysis import analyze_event
from .storage import get_store, EventStore
from .alerts import get_alert_manager, AlertManager


class EventCapture:
    """Captures events from agent function calls, LLM calls, and tool calls."""

    def __init__(
        self,
        store: Optional[EventStore] = None,
        alert_manager: Optional[AlertManager] = None,
        auto_classify: bool = True,
    ):
        self.store = store or get_store()
        self.alert_manager = alert_manager or get_alert_manager()
        self.auto_classify = auto_classify

    def capture_call(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
        event_type: str = "function_call",
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[list] = None,
    ) -> Any:
        """Execute a function and capture the event.

        Args:
            func: The function to call.
            args: Positional arguments.
            kwargs: Keyword arguments.
            event_type: Type of event (function_call, llm_call, tool_call).
            metadata: Additional metadata to store.
            tags: Tags for the event.

        Returns:
            The result of the function call.

        Raises:
            The original exception if the function fails.
        """
        event_id = str(uuid.uuid4())
        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Safely serialize args
        try:
            safe_args = {"args": _safe_repr(args), "kwargs": _safe_repr(kwargs)}
        except Exception:
            safe_args = {"args": str(args), "kwargs": str(kwargs)}

        event: Dict[str, Any] = {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "function_name": getattr(func, "__qualname__", getattr(func, "__name__", str(func))),
            "args": safe_args,
            "success": True,
            "metadata": metadata or {},
            "tags": tags or [],
        }

        try:
            result = func(*args, **kwargs)
            elapsed = (time.monotonic() - start_time) * 1000
            event["duration_ms"] = round(elapsed, 2)
            event["result"] = _safe_repr(result)
            event["success"] = True

            # Check for silent failures (empty results that might indicate problems)
            if self.auto_classify and (result is None or result == "" or result == {} or result == []):
                event["success"] = False
                event["root_cause"] = analyze_event(event)

            self.store.store_event(event)
            return result

        except Exception as e:
            elapsed = (time.monotonic() - start_time) * 1000
            event["duration_ms"] = round(elapsed, 2)
            event["success"] = False
            event["error_message"] = str(e)
            event["error_type"] = type(e).__name__
            event["traceback"] = tb.format_exc()

            if self.auto_classify:
                event["root_cause"] = analyze_event(event)

            self.store.store_event(event)

            # Send alert for failures
            try:
                self.alert_manager.send_alert(event)
            except Exception:
                pass  # Don't let alert failures break the agent

            raise

    def log_event(self, event: Dict[str, Any]) -> None:
        """Manually log an event.

        Args:
            event: Event dictionary. Must contain at least event_type.
        """
        if "event_id" not in event:
            event["event_id"] = str(uuid.uuid4())
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "success" not in event:
            event["success"] = True

        if self.auto_classify and not event.get("success"):
            event["root_cause"] = analyze_event(event)

        self.store.store_event(event)

        if not event.get("success"):
            try:
                self.alert_manager.send_alert(event)
            except Exception:
                pass


def _safe_repr(obj: Any, max_length: int = 1000) -> Any:
    """Create a safe, serializable representation of an object."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        s = obj
        if isinstance(s, str) and len(s) > max_length:
            return s[:max_length] + "...(truncated)"
        return s
    if isinstance(obj, (list, tuple)):
        if len(obj) > 20:
            return [_safe_repr(x, max_length) for x in obj[:20]] + [f"...({len(obj) - 20} more)"]
        return [_safe_repr(x, max_length) for x in obj]
    if isinstance(obj, dict):
        items = list(obj.items())
        if len(items) > 20:
            result = {str(k): _safe_repr(v, max_length) for k, v in items[:20]}
            result["__truncated__"] = f"{len(items) - 20} more keys"
            return result
        return {str(k): _safe_repr(v, max_length) for k, v in items}
    try:
        s = str(obj)
        if len(s) > max_length:
            return s[:max_length] + "...(truncated)"
        return s
    except Exception:
        return f"<unserializable: {type(obj).__name__}>"


# Global capture instance
_default_capture: Optional[EventCapture] = None


def get_capture(**kwargs) -> EventCapture:
    """Get or create the default event capture."""
    global _default_capture
    if _default_capture is None:
        _default_capture = EventCapture(**kwargs)
    return _default_capture


def reset_capture() -> None:
    """Reset the default capture (useful for testing)."""
    global _default_capture
    _default_capture = None
