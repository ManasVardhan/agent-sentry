"""LlamaIndex callback handler integration for agent-sentry.

Attach :class:`AgentSentryLlamaIndexHandler` to LlamaIndex's callback
manager and every LLM call, tool call, and query is captured as an
agent-sentry event, including failures with root cause classification.

Works with or without llama-index-core installed: when the package is
available the handler subclasses LlamaIndex's BaseCallbackHandler, and
otherwise a minimal shim provides the attributes CallbackManager needs.

Usage:
    from llama_index.core import Settings
    from llama_index.core.callbacks import CallbackManager
    from agent_sentry.integrations.llamaindex import AgentSentryLlamaIndexHandler

    handler = AgentSentryLlamaIndexHandler()
    Settings.callback_manager = CallbackManager([handler])
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..capture import get_capture, EventCapture

try:
    from llama_index.core.callbacks.base_handler import (
        BaseCallbackHandler as _BaseHandler,
    )
except ImportError:  # llama-index-core is optional

    class _BaseHandler:  # type: ignore[no-redef]
        """Minimal stand-in exposing the attributes CallbackManager expects."""

        def __init__(self, event_starts_to_ignore=None, event_ends_to_ignore=None):
            self.event_starts_to_ignore = tuple(event_starts_to_ignore or [])
            self.event_ends_to_ignore = tuple(event_ends_to_ignore or [])


_TRUNCATE_AT = 500

# LlamaIndex event types mapped to agent-sentry event types.
_EVENT_TYPE_MAP = {
    "llm": "llm_call",
    "function_call": "tool_call",
    "query": "function_call",
    "sub_question": "function_call",
    "agent_step": "function_call",
}


def _safe(value: Any, limit: int = _TRUNCATE_AT) -> str:
    """Stringify any payload value defensively, truncating long output."""
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        text = f"<unprintable {type(value).__name__}>"
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def _event_name(event_type: Any) -> str:
    """Normalize a CBEventType (str enum) or plain string to its value."""
    return str(getattr(event_type, "value", event_type))


def _llm_function_name(payload: Dict[str, Any]) -> str:
    """Best-effort model name from an LLM event payload."""
    serialized = payload.get("serialized")
    if isinstance(serialized, dict):
        for key in ("model", "model_name"):
            if serialized.get(key):
                return str(serialized[key])
    if payload.get("model_name"):
        return str(payload["model_name"])
    return "llamaindex_llm"


def _llm_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract prompt or messages from an LLM start payload."""
    messages = payload.get("messages")
    if isinstance(messages, list):
        return {
            "messages": [
                {
                    "role": str(getattr(m, "role", type(m).__name__)),
                    "content": _safe(getattr(m, "content", m)),
                }
                for m in messages[:5]
            ]
        }
    prompt = payload.get("formatted_prompt")
    if prompt is not None:
        return {"prompt": _safe(prompt)}
    return {}


def _llm_response_text(payload: Dict[str, Any]) -> Optional[str]:
    """Extract response text from an LLM end payload, tolerating odd shapes."""
    response = payload.get("response")
    if response is not None:
        content = getattr(getattr(response, "message", None), "content", None)
        if content is not None:
            return _safe(content)
        return _safe(response)
    completion = payload.get("completion")
    if completion is not None:
        text = getattr(completion, "text", None)
        return _safe(text if text is not None else completion)
    return None


def _token_usage(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract token usage from an LLM end payload across response shapes."""
    for key in ("response", "completion"):
        raw = getattr(payload.get(key), "raw", None)
        usage: Any = None
        if isinstance(raw, dict):
            usage = raw.get("usage")
        elif raw is not None:
            usage = getattr(raw, "usage", None)
        if isinstance(usage, dict):
            return dict(usage)
        if usage is not None:
            fields = ("prompt_tokens", "completion_tokens", "total_tokens")
            found = {
                f: getattr(usage, f)
                for f in fields
                if isinstance(getattr(usage, f, None), int)
            }
            if found:
                return found
    return None


def _tool_function_name(payload: Dict[str, Any]) -> str:
    """Best-effort tool name from a function_call event payload."""
    tool = payload.get("tool")
    name = getattr(tool, "name", None)
    if name:
        return str(name)
    if isinstance(tool, str) and tool:
        return tool
    return "llamaindex_tool"


class AgentSentryLlamaIndexHandler(_BaseHandler):
    """LlamaIndex callback handler that captures events to agent-sentry.

    LLM calls become llm_call events with prompt, response, and token
    usage. Tool calls become tool_call events with input and result.
    Queries, sub-questions, and agent steps become function_call events
    with duration. Exceptions surfaced by LlamaIndex are recorded as
    failures and classified like any other agent-sentry event.
    """

    def __init__(
        self,
        capture: Optional[EventCapture] = None,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
        self.capture = capture or get_capture()
        self.tags = tags or ["llamaindex"]
        self._events: Dict[str, Dict[str, Any]] = {}

    # -- trace lifecycle (required by BaseCallbackHandler) ------------------

    def start_trace(self, trace_id: Optional[str] = None) -> None:
        """Called by LlamaIndex when a trace starts. Nothing to do."""

    def end_trace(
        self,
        trace_id: Optional[str] = None,
        trace_map: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Called by LlamaIndex when a trace ends. Nothing to do."""

    # -- event lifecycle ----------------------------------------------------

    def on_event_start(
        self,
        event_type: Any,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        parent_id: str = "",
        **kwargs: Any,
    ) -> str:
        event_id = event_id or str(uuid.uuid4())
        payload = payload or {}
        name = _event_name(event_type)

        if name == "exception":
            self._log_exception(payload, source="exception")
            return event_id

        if name not in _EVENT_TYPE_MAP:
            return event_id

        pending: Dict[str, Any] = {
            "start_time": time.monotonic(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": _EVENT_TYPE_MAP[name],
            "llamaindex_type": name,
        }
        if name == "llm":
            pending["function_name"] = _llm_function_name(payload)
            pending["args"] = _llm_input(payload)
        elif name == "function_call":
            pending["function_name"] = _tool_function_name(payload)
            pending["args"] = {"input": _safe(payload.get("function_call"))}
        else:
            pending["function_name"] = name
            args: Dict[str, Any] = {}
            if payload.get("query_str") is not None:
                args["query"] = _safe(payload["query_str"])
            if payload.get("sub_question") is not None:
                args["sub_question"] = _safe(payload["sub_question"])
            pending["args"] = args

        self._events[event_id] = pending
        return event_id

    def on_event_end(
        self,
        event_type: Any,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> None:
        payload = payload or {}
        name = _event_name(event_type)

        if name == "exception" or ("exception" in payload and event_id not in self._events):
            self._log_exception(payload, source=name)
            return

        pending = self._events.pop(event_id, None)
        if pending is None:
            return

        elapsed = (time.monotonic() - pending["start_time"]) * 1000
        event: Dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "timestamp": pending["timestamp"],
            "event_type": pending["event_type"],
            "function_name": pending["function_name"],
            "args": pending["args"],
            "duration_ms": round(elapsed, 2),
            "tags": self.tags,
        }

        error = payload.get("exception")
        if error is not None:
            event["success"] = False
            event["error_message"] = _safe(error)
            event["error_type"] = (
                type(error).__name__ if isinstance(error, BaseException) else "Exception"
            )
            self.capture.log_event(event)
            return

        event["success"] = True
        lname = pending["llamaindex_type"]
        if lname == "llm":
            event["result"] = _llm_response_text(payload)
            usage = _token_usage(payload)
            if usage:
                event["token_usage"] = usage
        elif lname == "function_call":
            event["result"] = _safe(payload.get("function_call_response"))
        else:
            response = payload.get("response")
            if response is not None:
                event["result"] = _safe(response)

        self.capture.log_event(event)

    # -- helpers ------------------------------------------------------------

    def _log_exception(self, payload: Dict[str, Any], source: str) -> None:
        """Record a standalone exception event surfaced by LlamaIndex."""
        error = payload.get("exception")
        self.capture.log_event(
            {
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": "function_call",
                "function_name": f"llamaindex_{source}",
                "success": False,
                "error_message": _safe(error) if error is not None else "unknown error",
                "error_type": (
                    type(error).__name__
                    if isinstance(error, BaseException)
                    else "Exception"
                ),
                "tags": self.tags,
            }
        )
