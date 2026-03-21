"""OpenAI client wrapper for agent-sentry."""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..capture import get_capture, EventCapture


class SentryOpenAIWrapper:
    """Wrapper around OpenAI client that captures all API calls.

    Usage:
        from openai import OpenAI
        from agent_sentry.integrations.openai import SentryOpenAIWrapper

        client = OpenAI()
        sentry_client = SentryOpenAIWrapper(client)

        # Use exactly like the OpenAI client
        response = sentry_client.chat_completions_create(
            model="gpt-4",
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
        self.tags = tags or ["openai"]

    def chat_completions_create(self, **kwargs: Any) -> Any:
        """Wrap chat.completions.create with event capture."""
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])

        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            response = self.client.chat.completions.create(**kwargs)
            elapsed = (time.monotonic() - start_time) * 1000

            # Extract token usage
            token_usage = None
            cost = None
            result_text = None

            if hasattr(response, "usage") and response.usage:
                token_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                cost = _estimate_cost(model, token_usage)

            if hasattr(response, "choices") and response.choices:
                choice = response.choices[0]
                if hasattr(choice, "message") and choice.message:
                    result_text = getattr(choice.message, "content", None)

            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "event_type": "llm_call",
                "function_name": f"openai.{model}",
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
                "function_name": f"openai.{model}",
                "args": {"model": model, "message_count": len(messages)},
                "success": False,
                "error_message": str(e),
                "error_type": type(e).__name__,
                "duration_ms": round(elapsed, 2),
                "tags": self.tags,
            }
            self.capture.log_event(event)
            raise

    def completions_create(self, **kwargs: Any) -> Any:
        """Wrap completions.create with event capture."""
        model = kwargs.get("model", "unknown")
        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            response = self.client.completions.create(**kwargs)
            elapsed = (time.monotonic() - start_time) * 1000

            event = {
                "event_id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "event_type": "llm_call",
                "function_name": f"openai.{model}",
                "success": True,
                "duration_ms": round(elapsed, 2),
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
                "function_name": f"openai.{model}",
                "success": False,
                "error_message": str(e),
                "error_type": type(e).__name__,
                "duration_ms": round(elapsed, 2),
                "tags": self.tags,
            }
            self.capture.log_event(event)
            raise


# Cost estimates per 1K tokens (USD)
# Order matters: more specific names must come before prefixes (e.g. gpt-4o-mini before gpt-4o)
_COST_TABLE: Dict[str, Dict[str, float]] = {
    "gpt-4.1-nano": {"prompt": 0.0001, "completion": 0.0004},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "o4-mini": {"prompt": 0.0011, "completion": 0.0044},
    "o3-mini": {"prompt": 0.0011, "completion": 0.0044},
    "o3": {"prompt": 0.01, "completion": 0.04},
    "o1-mini": {"prompt": 0.003, "completion": 0.012},
    "o1": {"prompt": 0.015, "completion": 0.06},
}


def _estimate_cost(model: str, token_usage: Dict[str, int]) -> Optional[float]:
    """Estimate cost based on model and token usage."""
    # Find matching model
    cost_entry = None
    for key in _COST_TABLE:
        if key in model:
            cost_entry = _COST_TABLE[key]
            break
    if not cost_entry:
        return None

    prompt_cost = (token_usage.get("prompt_tokens", 0) / 1000) * cost_entry["prompt"]
    completion_cost = (token_usage.get("completion_tokens", 0) / 1000) * cost_entry["completion"]
    return round(prompt_cost + completion_cost, 6)
