"""Anomaly detection on failure patterns.

Buckets events into fixed time windows per function and flags buckets that
deviate sharply from that function's own baseline:

    - failure_spike: a bucket's failure rate is far above the function's
      typical failure rate across its other buckets.
    - latency_spike: a bucket's average duration is far above the
      function's typical average duration.
    - new_root_cause: a root cause shows up that the function has never
      failed with before (only after the function already has a failure
      history, so a first-ever failure is not "new").

Detection is self-calibrating: baselines come from the event history
itself, so a flaky function with a steady 20 percent failure rate is not
flagged, while a normally reliable function jumping to 50 percent is.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_MIN_RATE_STD = 0.05
_MIN_LATENCY_STD_FRACTION = 0.1


@dataclass
class Anomaly:
    """A detected deviation from a function's baseline behavior."""

    type: str
    function_name: str
    bucket_start: str
    bucket_end: str
    observed: float
    baseline: float
    deviation: float
    events: int
    failures: int
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {
            "type": self.type,
            "function_name": self.function_name,
            "bucket_start": self.bucket_start,
            "bucket_end": self.bucket_end,
            "observed": round(self.observed, 4),
            "baseline": round(self.baseline, 4),
            "deviation": round(self.deviation, 2),
            "events": self.events,
            "failures": self.failures,
            "details": dict(self.details),
        }


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string into an aware UTC datetime."""
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


def _mean_std(values: List[float]) -> "tuple[float, float]":
    """Mean and population standard deviation."""
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, variance**0.5


def _bucket_bounds(index: int, bucket_seconds: int) -> "tuple[str, str]":
    start = datetime.fromtimestamp(index * bucket_seconds, tz=timezone.utc)
    end = datetime.fromtimestamp((index + 1) * bucket_seconds, tz=timezone.utc)
    return start.isoformat(), end.isoformat()


def detect_anomalies(
    events: List[Dict[str, Any]],
    bucket_minutes: int = 60,
    threshold: float = 3.0,
    min_events: int = 5,
    min_buckets: int = 3,
) -> List[Anomaly]:
    """Detect failure-pattern anomalies in a list of events.

    Events are grouped per function into fixed time buckets. Each bucket
    with at least min_events events is compared to the function's other
    buckets (leave-one-out baseline): a bucket whose failure rate or
    average duration sits more than threshold standard deviations above
    the baseline mean is flagged. Root causes never seen in earlier
    buckets are flagged once the function has prior failure history.

    Args:
        events: Event dicts (as returned by EventStore.get_events).
        bucket_minutes: Bucket width in minutes (default: 60).
        threshold: Standard deviations above baseline to flag (default: 3.0).
        min_events: Minimum events in a bucket to evaluate spikes (default: 5).
        min_buckets: Minimum buckets a function needs for spike detection
            (default: 3).

    Returns:
        Anomalies ordered by bucket start time, then function name.

    Raises:
        ValueError: If bucket_minutes, threshold, min_events, or
            min_buckets is not positive.
    """
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    if min_events <= 0:
        raise ValueError("min_events must be positive")
    if min_buckets <= 0:
        raise ValueError("min_buckets must be positive")

    bucket_seconds = bucket_minutes * 60
    grouped: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for event in events:
        name = event.get("function_name")
        ts = _parse_ts(event.get("timestamp"))
        if not name or ts is None:
            continue
        index = int(ts.timestamp()) // bucket_seconds
        grouped.setdefault(name, {}).setdefault(index, []).append(event)

    anomalies: List[Anomaly] = []
    for name, buckets in grouped.items():
        indices = sorted(buckets)
        stats = {}
        for index in indices:
            bucket = buckets[index]
            failures = sum(1 for e in bucket if not e.get("success", True))
            durations = [e.get("duration_ms") or 0.0 for e in bucket]
            stats[index] = {
                "events": len(bucket),
                "failures": failures,
                "rate": failures / len(bucket),
                "avg_duration": sum(durations) / len(durations),
            }

        if len(indices) >= min_buckets:
            for index in indices:
                current = stats[index]
                if current["events"] < min_events:
                    continue
                others_rate = [stats[i]["rate"] for i in indices if i != index]
                others_lat = [stats[i]["avg_duration"] for i in indices if i != index]
                start, end = _bucket_bounds(index, bucket_seconds)

                mean, std = _mean_std(others_rate)
                std = max(std, _MIN_RATE_STD)
                deviation = (current["rate"] - mean) / std
                if current["failures"] > 0 and deviation >= threshold:
                    anomalies.append(Anomaly(
                        type="failure_spike",
                        function_name=name,
                        bucket_start=start,
                        bucket_end=end,
                        observed=current["rate"],
                        baseline=mean,
                        deviation=deviation,
                        events=current["events"],
                        failures=current["failures"],
                    ))

                mean, std = _mean_std(others_lat)
                std = max(std, mean * _MIN_LATENCY_STD_FRACTION, 1.0)
                deviation = (current["avg_duration"] - mean) / std
                if deviation >= threshold:
                    anomalies.append(Anomaly(
                        type="latency_spike",
                        function_name=name,
                        bucket_start=start,
                        bucket_end=end,
                        observed=current["avg_duration"],
                        baseline=mean,
                        deviation=deviation,
                        events=current["events"],
                        failures=current["failures"],
                    ))

        seen_causes: "set[str]" = set()
        for index in indices:
            bucket = buckets[index]
            causes = {
                (e.get("root_cause") or "unknown")
                for e in bucket
                if not e.get("success", True)
            }
            new_causes = causes - seen_causes
            if seen_causes and new_causes:
                start, end = _bucket_bounds(index, bucket_seconds)
                current = stats[index]
                for cause in sorted(new_causes):
                    anomalies.append(Anomaly(
                        type="new_root_cause",
                        function_name=name,
                        bucket_start=start,
                        bucket_end=end,
                        observed=float(sum(
                            1 for e in bucket
                            if not e.get("success", True)
                            and (e.get("root_cause") or "unknown") == cause
                        )),
                        baseline=0.0,
                        deviation=0.0,
                        events=current["events"],
                        failures=current["failures"],
                        details={"root_cause": cause},
                    ))
            seen_causes |= causes

    anomalies.sort(key=lambda a: (a.bucket_start, a.function_name, a.type))
    return anomalies


def summarize_anomalies(anomalies: List[Anomaly]) -> Dict[str, Any]:
    """Aggregate anomalies into summary stats.

    Returns the total count, counts per anomaly type, the number of
    affected functions, and the function with the most anomalies.
    """
    by_type: Dict[str, int] = {}
    by_function: Dict[str, int] = {}
    for anomaly in anomalies:
        by_type[anomaly.type] = by_type.get(anomaly.type, 0) + 1
        by_function[anomaly.function_name] = by_function.get(anomaly.function_name, 0) + 1
    most_affected = max(by_function, key=lambda k: by_function[k]) if by_function else None
    return {
        "total_anomalies": len(anomalies),
        "by_type": by_type,
        "functions_affected": len(by_function),
        "most_affected_function": most_affected,
    }
