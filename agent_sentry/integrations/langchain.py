"""LangChain callback handler integration for agent-sentry."""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from ..capture import get_capture, EventCapture


class AgentSentryCallbackHandler:
    """LangChain callback handler that captures events to agent-sentry.

    Usage:
        from agent_sentry.integrations.langchain import AgentSentryCallbackHandler

        handler = AgentSentryCallbackHandler()
        llm = ChatOpenAI(callbacks=[handler])
    """

    def __init__(self, capture: Optional[EventCapture] = None, tags: Optional[List[str]] = None):
        self.capture = capture or get_capture()
        self.tags = tags or ["langchain"]
        self._runs: Dict[str, Dict[str, Any]] = {}

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts running."""
        rid = str(run_id or uuid.uuid4())
        self._runs[rid] = {
            "start_time": time.monotonic(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "llm_call",
            "function_name": serialized.get("id", ["unknown"])[-1] if serialized.get("id") else "unknown_llm",
            "prompts": prompts[:3],  # Capture first 3 prompts
        }

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM finishes."""
        rid = str(run_id or "")
        run_data = self._runs.pop(rid, None)
        if not run_data:
            return

        elapsed = (time.monotonic() - run_data["start_time"]) * 1000

        # Extract token usage if available
        token_usage = None
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage")

        result_text = ""
        if hasattr(response, "generations") and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "text"):
                        result_text += gen.text

        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": run_data["timestamp"],
            "event_type": "llm_call",
            "function_name": run_data["function_name"],
            "args": {"prompts": run_data["prompts"]},
            "result": result_text[:500] if result_text else None,
            "success": True,
            "duration_ms": round(elapsed, 2),
            "token_usage": token_usage,
            "tags": self.tags,
        }
        self.capture.log_event(event)

    def on_llm_error(
        self,
        error: Union[Exception, KeyboardInterrupt],
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM errors."""
        rid = str(run_id or "")
        run_data = self._runs.pop(rid, None)
        start_time = run_data["start_time"] if run_data else time.monotonic()
        elapsed = (time.monotonic() - start_time) * 1000

        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": run_data["timestamp"] if run_data else datetime.now(timezone.utc).isoformat(),
            "event_type": "llm_call",
            "function_name": run_data["function_name"] if run_data else "unknown_llm",
            "success": False,
            "error_message": str(error),
            "error_type": type(error).__name__,
            "duration_ms": round(elapsed, 2),
            "tags": self.tags,
        }
        self.capture.log_event(event)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts."""
        rid = str(run_id or uuid.uuid4())
        self._runs[rid] = {
            "start_time": time.monotonic(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "tool_call",
            "function_name": serialized.get("name", "unknown_tool"),
            "input": input_str[:500],
        }

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool finishes."""
        rid = str(run_id or "")
        run_data = self._runs.pop(rid, None)
        if not run_data:
            return

        elapsed = (time.monotonic() - run_data["start_time"]) * 1000
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": run_data["timestamp"],
            "event_type": "tool_call",
            "function_name": run_data["function_name"],
            "args": {"input": run_data.get("input")},
            "result": str(output)[:500],
            "success": True,
            "duration_ms": round(elapsed, 2),
            "tags": self.tags,
        }
        self.capture.log_event(event)

    def on_tool_error(
        self,
        error: Union[Exception, KeyboardInterrupt],
        *,
        run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool errors."""
        rid = str(run_id or "")
        run_data = self._runs.pop(rid, None)
        start_time = run_data["start_time"] if run_data else time.monotonic()
        elapsed = (time.monotonic() - start_time) * 1000

        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": run_data["timestamp"] if run_data else datetime.now(timezone.utc).isoformat(),
            "event_type": "tool_call",
            "function_name": run_data["function_name"] if run_data else "unknown_tool",
            "args": {"input": run_data.get("input")} if run_data else None,
            "success": False,
            "error_message": str(error),
            "error_type": type(error).__name__,
            "duration_ms": round(elapsed, 2),
            "tags": self.tags,
        }
        self.capture.log_event(event)

    # LangChain also calls these, provide no-ops
    def on_chain_start(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on_chain_end(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on_chain_error(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on_text(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on_agent_action(self, *args: Any, **kwargs: Any) -> None:
        pass

    def on_agent_finish(self, *args: Any, **kwargs: Any) -> None:
        pass
