"""Retry pattern detection for agent events.

Detects retry sequences: repeated calls to the same function where a failure
is followed by one or more follow-up attempts within a short time window.
A sequence "recovered" if the final attempt succeeded, otherwise it is
"exhausted" (the caller gave up or is still failing).

This surfaces two costly patterns:
    - Retry storms: functions burning time and money on repeated failures.
    - Silent flakiness: functions that usually recover but waste attempts.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class RetrySequence:
    """A detected sequence of retried calls to one function."""

    function_name: str
    attempts: int = 0
    failures: int = 0
    recovered: bool = False
    first_timestamp: str = ""
    last_timestamp: str = ""
    total_duration_ms: float = 0.0
    wasted_duration_ms: float = 0.0
    wasted_cost: float = 0.0
    root_causes: Dict[str, int] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        """Human-readable outcome: 'recovered' or 'exhausted'."""
        return "recovered" if self.recovered else "exhausted"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {
            "function_name": self.function_name,
            "attempts": self.attempts,
            "failures": self.failures,
            "outcome": self.outcome,
            "recovered": self.recovered,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "wasted_duration_ms": round(self.wasted_duration_ms, 2),
            "wasted_cost": round(self.wasted_cost, 6),
            "root_causes": dict(self.root_causes),
        }


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string into an aware UTC datetime.

    Returns None when the value is missing or unparseable. Naive datetimes
    are assumed to be UTC so mixed naive/aware data never raises.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finalize(seq_events: List[Dict[str, Any]], min_attempts: int) -> Optional[RetrySequence]:
    """Build a RetrySequence from consecutive events, or None if not a retry."""
    if len(seq_events) < min_attempts:
        return None
    failures = sum(1 for e in seq_events if not e.get("success", True))
    if failures == 0:
        return None

    seq = RetrySequence(function_name=seq_events[0].get("function_name") or "unknown")
    seq.attempts = len(seq_events)
    seq.failures = failures
    seq.recovered = bool(seq_events[-1].get("success", True))
    seq.first_timestamp = seq_events[0].get("timestamp") or ""
    seq.last_timestamp = seq_events[-1].get("timestamp") or ""
    for event in seq_events:
        duration = event.get("duration_ms") or 0.0
        cost = event.get("cost") or 0.0
        seq.total_duration_ms += duration
        if not event.get("success", True):
            seq.wasted_duration_ms += duration
            seq.wasted_cost += cost
            cause = event.get("root_cause") or "unknown"
            seq.root_causes[cause] = seq.root_causes.get(cause, 0) + 1
    return seq


def detect_retry_sequences(
    events: List[Dict[str, Any]],
    window_seconds: float = 60.0,
    min_attempts: int = 2,
) -> List[RetrySequence]:
    """Detect retry sequences in a list of events.

    Events are grouped per function and sorted by timestamp. A sequence
    starts at a failed call; follow-up calls to the same function extend it
    while the gap between consecutive calls stays within window_seconds.
    A successful call closes the sequence as recovered. Sequences shorter
    than min_attempts, or with no failures, are ignored.

    Args:
        events: Event dicts (as returned by EventStore.get_events).
        window_seconds: Max gap between consecutive attempts (default: 60).
        min_attempts: Minimum calls to count as a retry sequence (default: 2).

    Returns:
        Detected sequences, ordered by first attempt time.

    Raises:
        ValueError: If window_seconds <= 0 or min_attempts < 2.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if min_attempts < 2:
        raise ValueError("min_attempts must be at least 2")

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        name = event.get("function_name")
        if not name:
            continue
        grouped.setdefault(name, []).append(event)

    sequences: List[RetrySequence] = []
    for name, group in grouped.items():
        group.sort(key=lambda e: e.get("timestamp") or "")
        current: List[Dict[str, Any]] = []
        prev_ts: Optional[datetime] = None
        for event in group:
            ts = _parse_ts(event.get("timestamp"))
            in_window = (
                current
                and ts is not None
                and prev_ts is not None
                and (ts - prev_ts).total_seconds() <= window_seconds
            )
            if current and not in_window:
                seq = _finalize(current, min_attempts)
                if seq:
                    sequences.append(seq)
                current = []

            success = bool(event.get("success", True))
            if current:
                current.append(event)
                if success:
                    seq = _finalize(current, min_attempts)
                    if seq:
                        sequences.append(seq)
                    current = []
            elif not success:
                current = [event]
            prev_ts = ts
        seq = _finalize(current, min_attempts)
        if seq:
            sequences.append(seq)

    sequences.sort(key=lambda s: s.first_timestamp)
    return sequences


def summarize_retries(sequences: List[RetrySequence]) -> Dict[str, Any]:
    """Aggregate retry sequences into summary stats.

    Returns total sequences, recovered/exhausted counts, recovery rate
    (percent), total wasted duration and cost, and the most retried function.
    """
    recovered = sum(1 for s in sequences if s.recovered)
    total = len(sequences)
    by_function: Dict[str, int] = {}
    for seq in sequences:
        by_function[seq.function_name] = by_function.get(seq.function_name, 0) + 1
    most_retried = max(by_function, key=lambda k: by_function[k]) if by_function else None
    return {
        "total_sequences": total,
        "recovered": recovered,
        "exhausted": total - recovered,
        "recovery_rate": round((recovered / total) * 100, 2) if total else 0.0,
        "total_wasted_duration_ms": round(sum(s.wasted_duration_ms for s in sequences), 2),
        "total_wasted_cost": round(sum(s.wasted_cost for s in sequences), 6),
        "most_retried_function": most_retried,
    }
