"""Cost tracking analytics for agent events.

Aggregates the per-event cost estimates captured by the OpenAI and Anthropic
integrations (and any custom events that set a "cost" field) into spend
breakdowns by model, function, or day. Failed calls are tracked separately as
wasted cost so you can see how much money failures are burning.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


_VALID_GROUPINGS = ("model", "function", "day")


@dataclass
class CostBucket:
    """Aggregated spend for one grouping key (a model, function, or day)."""

    key: str
    cost: float = 0.0
    calls: int = 0
    failures: int = 0
    wasted_cost: float = 0.0
    tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {
            "key": self.key,
            "cost": round(self.cost, 6),
            "calls": self.calls,
            "failures": self.failures,
            "wasted_cost": round(self.wasted_cost, 6),
            "tokens": self.tokens,
        }


def _event_model(event: Dict[str, Any]) -> str:
    """Extract the model name from an event.

    Prefers args["model"] (set by the LLM integrations), then falls back to
    the suffix of a dotted function name like "openai.gpt-4".
    """
    args = event.get("args")
    if isinstance(args, dict):
        model = args.get("model")
        if isinstance(model, str) and model:
            return model
    name = event.get("function_name")
    if isinstance(name, str) and "." in name:
        prefix, _, suffix = name.partition(".")
        if prefix in ("openai", "anthropic") and suffix:
            return suffix
    return "unknown"


def _event_tokens(event: Dict[str, Any]) -> int:
    """Total token count for an event, handling OpenAI and Anthropic keys."""
    usage = event.get("token_usage")
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)):
        return int(total)
    count = 0
    for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            count += int(value)
    return count


def _bucket_key(event: Dict[str, Any], by: str) -> Optional[str]:
    """Grouping key for an event, or None if it cannot be bucketed."""
    if by == "model":
        return _event_model(event)
    if by == "function":
        return event.get("function_name") or "unknown"
    timestamp = event.get("timestamp")
    if isinstance(timestamp, str) and len(timestamp) >= 10:
        return timestamp[:10]
    return None


def aggregate_costs(
    events: List[Dict[str, Any]],
    by: str = "model",
) -> List[CostBucket]:
    """Aggregate event costs into buckets.

    Only events with a cost value are counted, so untracked function calls
    do not dilute the spend numbers.

    Args:
        events: Event dicts (as returned by EventStore.get_events).
        by: Grouping key: "model", "function", or "day".

    Returns:
        Buckets sorted by cost descending.

    Raises:
        ValueError: If by is not one of "model", "function", "day".
    """
    if by not in _VALID_GROUPINGS:
        raise ValueError(f"by must be one of {_VALID_GROUPINGS}, got {by!r}")

    buckets: Dict[str, CostBucket] = {}
    for event in events:
        cost = event.get("cost")
        if cost is None:
            continue
        key = _bucket_key(event, by)
        if key is None:
            continue
        bucket = buckets.setdefault(key, CostBucket(key=key))
        bucket.cost += float(cost)
        bucket.calls += 1
        bucket.tokens += _event_tokens(event)
        if not event.get("success", True):
            bucket.failures += 1
            bucket.wasted_cost += float(cost)

    result = list(buckets.values())
    result.sort(key=lambda b: (-b.cost, b.key))
    return result


def summarize_costs(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate events into a high-level spend summary.

    Returns total cost, tracked call count, total tokens, wasted cost from
    failed calls (with percentage), the most expensive model and function,
    and average cost per tracked call.
    """
    tracked = [e for e in events if e.get("cost") is not None]
    total_cost = sum(float(e["cost"]) for e in tracked)
    wasted_cost = sum(
        float(e["cost"]) for e in tracked if not e.get("success", True)
    )
    tokens = sum(_event_tokens(e) for e in tracked)

    by_model = aggregate_costs(tracked, by="model")
    by_function = aggregate_costs(tracked, by="function")

    return {
        "total_cost": round(total_cost, 6),
        "tracked_calls": len(tracked),
        "total_events": len(events),
        "total_tokens": tokens,
        "wasted_cost": round(wasted_cost, 6),
        "wasted_pct": round((wasted_cost / total_cost) * 100, 2) if total_cost else 0.0,
        "avg_cost_per_call": round(total_cost / len(tracked), 6) if tracked else 0.0,
        "top_model": by_model[0].key if by_model else None,
        "top_function": by_function[0].key if by_function else None,
    }
