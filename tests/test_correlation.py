"""Tests for failure correlation analysis."""

import json
import subprocess
import sys
import tempfile

import pytest

from agent_sentry.correlation import (
    FailureCluster,
    FunctionCorrelation,
    correlate_failures,
    find_failure_clusters,
    summarize_correlations,
)
from agent_sentry.storage import EventStore


def make_event(
    function_name="fetch_data",
    timestamp="2026-07-24T10:00:00+00:00",
    success=False,
    root_cause="timeout",
    event_id=None,
):
    return {
        "event_id": event_id or f"{function_name}-{timestamp}-{success}",
        "function_name": function_name,
        "timestamp": timestamp,
        "success": success,
        "root_cause": root_cause if not success else None,
        "event_type": "function_call",
    }


def ts(seconds):
    """Timestamp helper: seconds offset from a base time."""
    minute, sec = divmod(seconds, 60)
    return f"2026-07-24T10:{minute:02d}:{sec:02d}+00:00"


class TestFindFailureClusters:
    def test_empty_events(self):
        assert find_failure_clusters([]) == []

    def test_successes_ignored(self):
        events = [make_event(timestamp=ts(i), success=True) for i in range(5)]
        assert find_failure_clusters(events) == []

    def test_single_failure_below_min(self):
        events = [make_event(timestamp=ts(0))]
        assert find_failure_clusters(events) == []

    def test_two_close_failures_form_cluster(self):
        events = [make_event(timestamp=ts(0)), make_event("b", timestamp=ts(5))]
        clusters = find_failure_clusters(events)
        assert len(clusters) == 1
        assert clusters[0].failures == 2
        assert clusters[0].functions == {"fetch_data": 1, "b": 1}

    def test_gap_beyond_window_splits_clusters(self):
        events = [
            make_event(timestamp=ts(0)),
            make_event(timestamp=ts(5)),
            make_event(timestamp=ts(120)),
            make_event(timestamp=ts(125)),
        ]
        clusters = find_failure_clusters(events, window_seconds=30)
        assert len(clusters) == 2
        assert all(c.failures == 2 for c in clusters)

    def test_gap_exactly_window_stays_in_cluster(self):
        events = [make_event(timestamp=ts(0)), make_event(timestamp=ts(30))]
        clusters = find_failure_clusters(events, window_seconds=30)
        assert len(clusters) == 1

    def test_chain_rule_extends_cluster(self):
        # Each consecutive gap is 20s (within window) even though the
        # first-to-last span is 60s.
        events = [make_event(timestamp=ts(i * 20)) for i in range(4)]
        clusters = find_failure_clusters(events, window_seconds=30)
        assert len(clusters) == 1
        assert clusters[0].failures == 4
        assert clusters[0].span_seconds == pytest.approx(60.0)

    def test_unsorted_input_sorted_by_timestamp(self):
        events = [
            make_event(timestamp=ts(10)),
            make_event("b", timestamp=ts(0)),
            make_event("c", timestamp=ts(5)),
        ]
        clusters = find_failure_clusters(events)
        assert len(clusters) == 1
        assert clusters[0].start_timestamp == ts(0)
        assert clusters[0].end_timestamp == ts(10)

    def test_root_causes_counted(self):
        events = [
            make_event(timestamp=ts(0), root_cause="timeout"),
            make_event("b", timestamp=ts(2), root_cause="timeout"),
            make_event("c", timestamp=ts(4), root_cause="rate_limit"),
        ]
        clusters = find_failure_clusters(events)
        assert clusters[0].root_causes == {"timeout": 2, "rate_limit": 1}

    def test_missing_root_cause_becomes_unknown(self):
        events = [
            {"function_name": "a", "timestamp": ts(0), "success": False},
            {"function_name": "b", "timestamp": ts(1), "success": False},
        ]
        clusters = find_failure_clusters(events)
        assert clusters[0].root_causes == {"unknown": 2}

    def test_missing_function_name_becomes_unknown(self):
        events = [
            {"timestamp": ts(0), "success": False},
            {"timestamp": ts(1), "success": False},
        ]
        clusters = find_failure_clusters(events)
        assert clusters[0].functions == {"unknown": 2}

    def test_unparseable_timestamps_skipped(self):
        events = [
            make_event(timestamp=ts(0)),
            make_event("b", timestamp="not a timestamp"),
            make_event("c", timestamp=ts(3)),
        ]
        clusters = find_failure_clusters(events)
        assert clusters[0].failures == 2
        assert "b" not in clusters[0].functions

    def test_min_failures_filter(self):
        events = [
            make_event(timestamp=ts(0)),
            make_event(timestamp=ts(5)),
            make_event(timestamp=ts(120)),
        ]
        clusters = find_failure_clusters(events, min_failures=2)
        assert len(clusters) == 1
        clusters = find_failure_clusters(events, min_failures=1)
        assert len(clusters) == 2

    def test_z_suffix_timestamps(self):
        events = [
            make_event(timestamp="2026-07-24T10:00:00Z"),
            make_event("b", timestamp="2026-07-24T10:00:10Z"),
        ]
        clusters = find_failure_clusters(events)
        assert len(clusters) == 1

    def test_multi_function_property(self):
        single = FailureCluster(functions={"a": 2})
        multi = FailureCluster(functions={"a": 1, "b": 1})
        assert not single.multi_function
        assert multi.multi_function

    def test_span_seconds(self):
        events = [make_event(timestamp=ts(0)), make_event(timestamp=ts(12))]
        clusters = find_failure_clusters(events)
        assert clusters[0].span_seconds == pytest.approx(12.0)

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            find_failure_clusters([], window_seconds=0)
        with pytest.raises(ValueError):
            find_failure_clusters([], window_seconds=-1)

    def test_invalid_min_failures_raises(self):
        with pytest.raises(ValueError):
            find_failure_clusters([], min_failures=0)

    def test_to_dict(self):
        events = [
            make_event(timestamp=ts(0)),
            make_event("b", timestamp=ts(5), root_cause="rate_limit"),
        ]
        d = find_failure_clusters(events)[0].to_dict()
        assert d["failures"] == 2
        assert d["multi_function"] is True
        assert d["span_seconds"] == 5.0
        assert d["functions"] == {"fetch_data": 1, "b": 1}
        json.dumps(d)


def cluster_of(*names):
    functions = {}
    for name in names:
        functions[name] = functions.get(name, 0) + 1
    return FailureCluster(functions=functions, failures=len(names))


class TestCorrelateFailures:
    def test_empty(self):
        assert correlate_failures([]) == []

    def test_single_function_clusters_no_pairs(self):
        clusters = [cluster_of("a", "a"), cluster_of("a")]
        assert correlate_failures(clusters) == []

    def test_pair_below_min_co_filtered(self):
        clusters = [cluster_of("a", "b")]
        assert correlate_failures(clusters, min_co_occurrences=2) == []
        assert len(correlate_failures(clusters, min_co_occurrences=1)) == 1

    def test_jaccard_score_perfect_pair(self):
        clusters = [cluster_of("a", "b"), cluster_of("a", "b")]
        corr = correlate_failures(clusters)[0]
        assert corr.co_occurrences == 2
        assert corr.clusters_a == 2
        assert corr.clusters_b == 2
        assert corr.score == pytest.approx(1.0)

    def test_jaccard_score_partial_overlap(self):
        # a in 3 clusters, b in 2, together in 2: 2 / (3 + 2 - 2) = 0.6667
        clusters = [cluster_of("a", "b"), cluster_of("a", "b"), cluster_of("a")]
        corr = correlate_failures(clusters)[0]
        assert corr.score == pytest.approx(0.6667)

    def test_pair_names_sorted(self):
        clusters = [cluster_of("zeta", "alpha"), cluster_of("zeta", "alpha")]
        corr = correlate_failures(clusters)[0]
        assert corr.function_a == "alpha"
        assert corr.function_b == "zeta"

    def test_repeated_function_in_cluster_counts_once(self):
        clusters = [cluster_of("a", "a", "b"), cluster_of("a", "b")]
        corr = correlate_failures(clusters)[0]
        assert corr.co_occurrences == 2
        assert corr.clusters_a == 2

    def test_sorted_by_co_then_score(self):
        clusters = [
            cluster_of("a", "b"),
            cluster_of("a", "b"),
            cluster_of("a", "b"),
            cluster_of("c", "d"),
            cluster_of("c", "d"),
            cluster_of("c"),
        ]
        corrs = correlate_failures(clusters)
        assert (corrs[0].function_a, corrs[0].function_b) == ("a", "b")
        assert (corrs[1].function_a, corrs[1].function_b) == ("c", "d")

    def test_three_functions_all_pairs(self):
        clusters = [cluster_of("a", "b", "c"), cluster_of("a", "b", "c")]
        corrs = correlate_failures(clusters)
        pairs = {(c.function_a, c.function_b) for c in corrs}
        assert pairs == {("a", "b"), ("a", "c"), ("b", "c")}

    def test_invalid_min_co_raises(self):
        with pytest.raises(ValueError):
            correlate_failures([], min_co_occurrences=0)

    def test_to_dict(self):
        corr = FunctionCorrelation("a", "b", 2, 3, 2, 0.6667)
        d = corr.to_dict()
        assert d["function_a"] == "a"
        assert d["co_occurrences"] == 2
        assert d["score"] == 0.6667
        json.dumps(d)


class TestSummarizeCorrelations:
    def test_empty(self):
        summary = summarize_correlations([], [])
        assert summary["total_clusters"] == 0
        assert summary["multi_function_clusters"] == 0
        assert summary["largest_cluster"] == 0
        assert summary["most_involved_function"] is None
        assert summary["top_pair"] is None

    def test_populated(self):
        clusters = [
            cluster_of("a", "b"),
            cluster_of("a", "b", "c"),
            cluster_of("a"),
        ]
        corrs = correlate_failures(clusters)
        summary = summarize_correlations(clusters, corrs)
        assert summary["total_clusters"] == 3
        assert summary["multi_function_clusters"] == 2
        assert summary["largest_cluster"] == 3
        assert summary["most_involved_function"] == "a"
        assert summary["top_pair"] == "a + b"


class TestCorrelateCLI:
    def _seed_store(self, db_path):
        store = EventStore(db_path)
        # Cluster 1: search + summarize fail together (timeout cascade).
        store.store_event(make_event(
            "search", timestamp=ts(0), root_cause="timeout", event_id="e1",
        ))
        store.store_event(make_event(
            "summarize", timestamp=ts(5), root_cause="timeout", event_id="e2",
        ))
        # Unrelated success in between.
        store.store_event(make_event(
            "healthy", timestamp=ts(30), success=True, event_id="e3",
        ))
        # Cluster 2: same pair again, 10 minutes later.
        store.store_event(make_event(
            "search", timestamp=ts(600), root_cause="rate_limit", event_id="e4",
        ))
        store.store_event(make_event(
            "summarize", timestamp=ts(610), root_cause="timeout", event_id="e5",
        ))
        # Lone failure far away: below min_failures.
        store.store_event(make_event(
            "misc", timestamp=ts(1800), root_cause="auth_error", event_id="e6",
        ))
        return store

    def _run(self, db_path, *extra):
        return subprocess.run(
            [sys.executable, "-m", "agent_sentry", "--db", db_path, "correlate", *extra],
            capture_output=True, text=True,
        )

    def test_table_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db)
            assert result.returncode == 0
            assert "Failure Correlation" in result.stdout
            assert "search" in result.stdout
            assert "summarize" in result.stdout
            assert "Correlated Function Pairs" in result.stdout
            assert "search + summarize" in result.stdout
            assert "Top pair: search + summarize" in result.stdout

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--json-output")
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["summary"]["total_clusters"] == 2
            assert data["summary"]["multi_function_clusters"] == 2
            assert len(data["correlations"]) == 1
            assert data["correlations"][0]["score"] == pytest.approx(1.0)

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            EventStore(db)
            result = self._run(db)
            assert result.returncode == 0
            assert "No failure clusters detected" in result.stdout

    def test_wide_window_merges_clusters(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--window", "3600", "--json-output")
            data = json.loads(result.stdout)
            assert data["summary"]["total_clusters"] == 1
            assert data["summary"]["largest_cluster"] == 5

    def test_min_co_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--min-co", "3", "--json-output")
            data = json.loads(result.stdout)
            assert data["correlations"] == []
            assert data["summary"]["top_pair"] is None

    def test_invalid_window_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            EventStore(db)
            result = self._run(db, "--window", "0")
            assert result.returncode == 1
            assert "Error" in result.stdout

    def test_invalid_min_co_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            EventStore(db)
            result = self._run(db, "--min-co", "0")
            assert result.returncode == 1
            assert "Error" in result.stdout

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_sentry", "correlate", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--window" in result.stdout
        assert "--min-failures" in result.stdout
        assert "--min-co" in result.stdout
