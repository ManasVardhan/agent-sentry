"""Failure correlation analysis for agent events.

Groups failures that happen close together in time into clusters, then
measures which functions tend to fail together across those clusters.
This surfaces cascading failures (one broken dependency taking down
several agent functions at once) and shared root causes that per-function
views hide.

Two building blocks:
    - find_failure_clusters: chains failures within a time window into
      FailureCluster groups, regardless of which function failed.
    - correlate_failures: scores function pairs by how often they appear
      in the same clusters (Jaccard similarity over cluster membership).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Tuple

from .retries import _parse_ts


@dataclass
class FailureCluster:
    """A group of failures that occurred close together in time."""

    start_timestamp: str = ""
    end_timestamp: str = ""
    failures: int = 0
    functions: Dict[str, int] = field(default_factory=dict)
    root_causes: Dict[str, int] = field(default_factory=dict)
    span_seconds: float = 0.0

    @property
    def multi_function(self) -> bool:
        """True when more than one function failed in this cluster."""
        return len(self.functions) > 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "failures": self.failures,
            "functions": dict(self.functions),
            "root_causes": dict(self.root_causes),
            "span_seconds": round(self.span_seconds, 2),
            "multi_function": self.multi_function,
        }


@dataclass
class FunctionCorrelation:
    """How often two functions fail in the same clusters."""

    function_a: str
    function_b: str
    co_occurrences: int
    clusters_a: int
    clusters_b: int
    score: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly)."""
        return {
            "function_a": self.function_a,
            "function_b": self.function_b,
            "co_occurrences": self.co_occurrences,
            "clusters_a": self.clusters_a,
            "clusters_b": self.clusters_b,
            "score": self.score,
        }


def find_failure_clusters(
    events: List[Dict[str, Any]],
    window_seconds: float = 30.0,
    min_failures: int = 2,
) -> List[FailureCluster]:
    """Group failed events into time-based clusters.

    Failures are sorted by timestamp; a failure joins the current cluster
    when the gap to the previous failure is at most window_seconds,
    otherwise it starts a new cluster. Function identity does not matter
    for clustering, which is the point: cascades cross function
    boundaries. Successful events and failures without a parseable
    timestamp are ignored.

    Args:
        events: Event dicts (as returned by EventStore.get_events).
        window_seconds: Max gap between consecutive failures (default: 30).
        min_failures: Minimum failures for a cluster to count (default: 2).

    Returns:
        Clusters ordered by start time.

    Raises:
        ValueError: If window_seconds <= 0 or min_failures < 1.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if min_failures < 1:
        raise ValueError("min_failures must be at least 1")

    failures: List[Tuple[Any, Dict[str, Any]]] = []
    for event in events:
        if event.get("success", True):
            continue
        ts = _parse_ts(event.get("timestamp"))
        if ts is None:
            continue
        failures.append((ts, event))
    failures.sort(key=lambda pair: pair[0])

    clusters: List[FailureCluster] = []
    current: List[Tuple[Any, Dict[str, Any]]] = []

    def close_current() -> None:
        if len(current) < min_failures:
            return
        cluster = FailureCluster(
            start_timestamp=current[0][1].get("timestamp") or "",
            end_timestamp=current[-1][1].get("timestamp") or "",
            failures=len(current),
            span_seconds=(current[-1][0] - current[0][0]).total_seconds(),
        )
        for _, event in current:
            name = event.get("function_name") or "unknown"
            cluster.functions[name] = cluster.functions.get(name, 0) + 1
            cause = event.get("root_cause") or "unknown"
            cluster.root_causes[cause] = cluster.root_causes.get(cause, 0) + 1
        clusters.append(cluster)

    for ts, event in failures:
        if current and (ts - current[-1][0]).total_seconds() > window_seconds:
            close_current()
            current = []
        current.append((ts, event))
    close_current()
    return clusters


def correlate_failures(
    clusters: List[FailureCluster],
    min_co_occurrences: int = 2,
) -> List[FunctionCorrelation]:
    """Score function pairs by shared cluster membership.

    For every pair of functions that fail in the same cluster at least
    min_co_occurrences times, computes a Jaccard score:
    co_occurrences / (clusters_a + clusters_b - co_occurrences).
    A score of 1.0 means the two functions only ever fail together.

    Args:
        clusters: Output of find_failure_clusters.
        min_co_occurrences: Minimum shared clusters to report a pair
            (default: 2).

    Returns:
        Correlations sorted by co-occurrences, then score, descending.

    Raises:
        ValueError: If min_co_occurrences < 1.
    """
    if min_co_occurrences < 1:
        raise ValueError("min_co_occurrences must be at least 1")

    membership: Dict[str, int] = {}
    pair_counts: Dict[FrozenSet[str], int] = {}
    for cluster in clusters:
        names = sorted(cluster.functions)
        for name in names:
            membership[name] = membership.get(name, 0) + 1
        for i, name_a in enumerate(names):
            for name_b in names[i + 1:]:
                key = frozenset((name_a, name_b))
                pair_counts[key] = pair_counts.get(key, 0) + 1

    correlations: List[FunctionCorrelation] = []
    for key, co in pair_counts.items():
        if co < min_co_occurrences:
            continue
        name_a, name_b = sorted(key)
        clusters_a = membership[name_a]
        clusters_b = membership[name_b]
        union = clusters_a + clusters_b - co
        correlations.append(
            FunctionCorrelation(
                function_a=name_a,
                function_b=name_b,
                co_occurrences=co,
                clusters_a=clusters_a,
                clusters_b=clusters_b,
                score=round(co / union, 4) if union else 0.0,
            )
        )
    correlations.sort(key=lambda c: (-c.co_occurrences, -c.score, c.function_a, c.function_b))
    return correlations


def summarize_correlations(
    clusters: List[FailureCluster],
    correlations: List[FunctionCorrelation],
) -> Dict[str, Any]:
    """Aggregate clusters and correlations into summary stats.

    Returns total and multi-function cluster counts, the largest cluster
    size, the function involved in the most clusters, and the strongest
    correlated pair (highest co-occurrences, ties broken by score).
    """
    membership: Dict[str, int] = {}
    for cluster in clusters:
        for name in cluster.functions:
            membership[name] = membership.get(name, 0) + 1
    most_involved = max(membership, key=lambda k: membership[k]) if membership else None
    top_pair = None
    if correlations:
        top = correlations[0]
        top_pair = f"{top.function_a} + {top.function_b}"
    return {
        "total_clusters": len(clusters),
        "multi_function_clusters": sum(1 for c in clusters if c.multi_function),
        "largest_cluster": max((c.failures for c in clusters), default=0),
        "most_involved_function": most_involved,
        "top_pair": top_pair,
    }
