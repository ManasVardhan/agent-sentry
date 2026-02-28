"""Anthropic client wrapper for agent-sentry."""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..capture import get_capture, EventCapture


class SentryAnthropicWrapper:
    """Wrapper around Anthropic client that captures all API calls.

    Usage:
        from anthropic import Anthropic
        from agent_sentry.integrations.anthropic import SentryAnthropicWrapper

        client = Anthropic()
        sentry_client = SentryAnthropicWrapper(client)

        response = sentry_client.messages_create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Hello"}]
        )
    """

    def __init__(
        self,
        client: Any,
        capture: Optional[EventCapture] = None,
        tags: Optional[List[str]] = None,
    ):
        self.client = client
        self.capture = capture or get_capture()
        self.tags = tags or ["anthropic"]

    def messages_create(self, **kwargs: Any) -> Any:
        """Wrap messages.create with event capture."""
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])

        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            response = self.client.messages.create(**kwargs)
            elapsed = (time.monotonic() - start_time) * 1000

            # Extract usage
            token_usage = None
            cost = None
            result_text = None

            if hasattr(response, "usage") and response.usage:
                token_usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                cost = _estimate_cost(model, token_usage)

            if hasattr(response, "content") and response.content:
                texts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                result_text = " ".join(texts)

            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "event_type": "llm_call",
                "function_name": f"anthropic.{model}",
                "args": {"model": model, "message_count": len(messages)},
                "result": result_text[:500] if result_text else None,
                "success": True,
                "duration_ms": round(elapsed, 2),
                "token_usage": token_usage,
                "cost": cost,
                "tags": self.tags,
            }
            self.capture.log_event(event)
            return response

        except Exception as e:
            elapsed = (time.monotonic() - start_time) * 1000
            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "event_type": "llm_call",
                "function_name": f"anthropic.{model}",
                "args": {"model": model, "message_count": len(messages)},
                "success": False,
                "error_message": str(e),
                "error_type": type(e).__name__,
                "duration_ms": round(elapsed, 2),
                "tags": self.tags,
            }
            self.capture.log_event(event)
            raise


# Cost estimates per 1K tokens (USD)
_COST_TABLE: Dict[str, Dict[str, float]] = {
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.001, "output": 0.005},
}


def _estimate_cost(model: str, token_usage: Dict[str, int]) -> Optional[float]:
    """Estimate cost based on model and token usage."""
    cost_entry = None
    for key in _COST_TABLE:
        if key in model:
            cost_entry = _COST_TABLE[key]
            break
    if not cost_entry:
        return None

    input_cost = (token_usage.get("input_tokens", 0) / 1000) * cost_entry["input"]
    output_cost = (token_usage.get("output_tokens", 0) / 1000) * cost_entry["output"]
    return round(input_cost + output_cost, 6)
