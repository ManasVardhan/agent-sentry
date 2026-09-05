"""Tests for failure-pattern anomaly detection."""

import json
import subprocess
import sys
import tempfile

import pytest

from agent_sentry.anomalies import (
    Anomaly,
    detect_anomalies,
    summarize_anomalies,
)
from agent_sentry.storage import EventStore


def make_event(
    function_name="fetch_data",
    timestamp="2026-07-13T10:00:00+00:00",
    success=True,
    duration_ms=100.0,
    root_cause=None,
    event_id=None,
):
    return {
        "event_id": event_id or f"{function_name}-{timestamp}-{success}-{duration_ms}",
        "function_name": function_name,
        "timestamp": timestamp,
        "success": success,
        "duration_ms": duration_ms,
        "root_cause": root_cause,
        "event_type": "function_call",
    }


def hour_ts(hour, minute=0):
    """Timestamp helper inside a given UTC hour bucket."""
    return f"2026-07-13T{hour:02d}:{minute:02d}:00+00:00"


def steady_hours(name, hours, per_hour=6, fail_every=None, duration_ms=100.0):
    """Build baseline events: per_hour events for each hour in hours."""
    events = []
    for hour in hours:
        for i in range(per_hour):
            fail = fail_every is not None and i % fail_every == 0
            events.append(make_event(
                name,
                timestamp=hour_ts(hour, minute=i * 5),
                success=not fail,
                root_cause="timeout" if fail else None,
                duration_ms=duration_ms,
                event_id=f"{name}-{hour}-{i}",
            ))
    return events


class TestValidation:
    def test_bad_bucket_minutes(self):
        with pytest.raises(ValueError, match="bucket_minutes"):
            detect_anomalies([], bucket_minutes=0)

    def test_bad_threshold(self):
        with pytest.raises(ValueError, match="threshold"):
            detect_anomalies([], threshold=0)

    def test_bad_min_events(self):
        with pytest.raises(ValueError, match="min_events"):
            detect_anomalies([], min_events=0)

    def test_bad_min_buckets(self):
        with pytest.raises(ValueError, match="min_buckets"):
            detect_anomalies([], min_buckets=0)

    def test_empty_events(self):
        assert detect_anomalies([]) == []

    def test_events_without_names_or_timestamps_skipped(self):
        events = [
            {"timestamp": hour_ts(10), "success": True},
            {"function_name": "x", "timestamp": None, "success": True},
            {"function_name": "x", "timestamp": "not a date", "success": True},
        ]
        assert detect_anomalies(events) == []


class TestFailureSpike:
    def test_spike_detected(self):
        events = steady_hours("api_call", hours=range(10, 15))
        for i in range(6):
            events.append(make_event(
                "api_call", timestamp=hour_ts(15, minute=i * 5),
                success=i >= 4, root_cause="timeout" if i < 4 else None,
                event_id=f"spike-{i}",
            ))
        anomalies = detect_anomalies(events)
        spikes = [a for a in anomalies if a.type == "failure_spike"]
        assert len(spikes) == 1
        spike = spikes[0]
        assert spike.function_name == "api_call"
        assert spike.observed == pytest.approx(4 / 6)
        assert spike.baseline == pytest.approx(0.0)
        assert spike.deviation >= 3.0
        assert spike.failures == 4
        assert "2026-07-13T15:00:00" in spike.bucket_start

    def test_steady_failure_rate_not_flagged(self):
        events = steady_hours("flaky", hours=range(10, 16), fail_every=3)
        anomalies = detect_anomalies(events)
        assert [a for a in anomalies if a.type == "failure_spike"] == []

    def test_small_bucket_ignored(self):
        events = steady_hours("api_call", hours=range(10, 15))
        events.append(make_event(
            "api_call", timestamp=hour_ts(15), success=False,
            root_cause="timeout", event_id="lone-failure",
        ))
        anomalies = detect_anomalies(events, min_events=5)
        assert [a for a in anomalies if a.type == "failure_spike"] == []

    def test_too_few_buckets_ignored(self):
        events = steady_hours("api_call", hours=[10])
        for i in range(6):
            events.append(make_event(
                "api_call", timestamp=hour_ts(11, minute=i * 5),
                success=False, root_cause="timeout", event_id=f"f-{i}",
            ))
        anomalies = detect_anomalies(events, min_buckets=3)
        assert [a for a in anomalies if a.type == "failure_spike"] == []

    def test_functions_isolated(self):
        events = steady_hours("healthy", hours=range(10, 16))
        events += steady_hours("broken", hours=range(10, 15))
        for i in range(6):
            events.append(make_event(
                "broken", timestamp=hour_ts(15, minute=i * 5),
                success=False, root_cause="timeout", event_id=f"b-{i}",
            ))
        anomalies = detect_anomalies(events)
        spikes = [a for a in anomalies if a.type == "failure_spike"]
        assert [a.function_name for a in spikes] == ["broken"]


class TestLatencySpike:
    def test_spike_detected(self):
        events = steady_hours("slow_api", hours=range(10, 15), duration_ms=100.0)
        events += steady_hours("slow_api", hours=[15], duration_ms=5000.0)
        anomalies = detect_anomalies(events)
        spikes = [a for a in anomalies if a.type == "latency_spike"]
        assert len(spikes) == 1
        assert spikes[0].observed == pytest.approx(5000.0)
        assert spikes[0].baseline == pytest.approx(100.0)

    def test_steady_latency_not_flagged(self):
        events = steady_hours("steady", hours=range(10, 16), duration_ms=250.0)
        anomalies = detect_anomalies(events)
        assert [a for a in anomalies if a.type == "latency_spike"] == []


class TestNewRootCause:
    def test_new_cause_flagged(self):
        events = steady_hours("api_call", hours=range(10, 14), fail_every=3)
        events.append(make_event(
            "api_call", timestamp=hour_ts(14), success=False,
            root_cause="auth_error", event_id="new-cause",
        ))
        anomalies = detect_anomalies(events)
        new = [a for a in anomalies if a.type == "new_root_cause"]
        assert len(new) == 1
        assert new[0].details["root_cause"] == "auth_error"
        assert "2026-07-13T14:00:00" in new[0].bucket_start

    def test_first_failure_not_flagged_as_new(self):
        events = steady_hours("api_call", hours=[10, 11])
        events.append(make_event(
            "api_call", timestamp=hour_ts(12), success=False,
            root_cause="timeout", event_id="first-fail",
        ))
        anomalies = detect_anomalies(events)
        assert [a for a in anomalies if a.type == "new_root_cause"] == []

    def test_repeated_cause_not_flagged(self):
        events = steady_hours("api_call", hours=range(10, 14), fail_every=3)
        events.append(make_event(
            "api_call", timestamp=hour_ts(14), success=False,
            root_cause="timeout", event_id="same-cause",
        ))
        anomalies = detect_anomalies(events)
        assert [a for a in anomalies if a.type == "new_root_cause"] == []

    def test_missing_cause_counted_as_unknown(self):
        events = steady_hours("api_call", hours=range(10, 13), fail_every=3)
        events.append(make_event(
            "api_call", timestamp=hour_ts(13), success=False,
            root_cause=None, event_id="unknown-cause",
        ))
        anomalies = detect_anomalies(events)
        new = [a for a in anomalies if a.type == "new_root_cause"]
        assert len(new) == 1
        assert new[0].details["root_cause"] == "unknown"


class TestOrderingAndSerialization:
    def test_sorted_by_bucket_start(self):
        events = steady_hours("a", hours=range(10, 15), fail_every=3)
        events.append(make_event(
            "a", timestamp=hour_ts(12), success=False,
            root_cause="auth_error", event_id="a-new",
        ))
        events += steady_hours("b", hours=range(10, 15), fail_every=3)
        events.append(make_event(
            "b", timestamp=hour_ts(11), success=False,
            root_cause="rate_limit", event_id="b-new",
        ))
        anomalies = detect_anomalies(events)
        starts = [a.bucket_start for a in anomalies]
        assert starts == sorted(starts)

    def test_to_dict_round_trips_json(self):
        anomaly = Anomaly(
            type="failure_spike", function_name="f",
            bucket_start="2026-07-13T15:00:00+00:00",
            bucket_end="2026-07-13T16:00:00+00:00",
            observed=0.66667, baseline=0.0, deviation=13.333,
            events=6, failures=4,
        )
        payload = json.loads(json.dumps(anomaly.to_dict()))
        assert payload["type"] == "failure_spike"
        assert payload["observed"] == 0.6667
        assert payload["deviation"] == 13.33


class TestSummarize:
    def test_empty(self):
        summary = summarize_anomalies([])
        assert summary["total_anomalies"] == 0
        assert summary["by_type"] == {}
        assert summary["functions_affected"] == 0
        assert summary["most_affected_function"] is None

    def test_counts(self):
        anomalies = [
            Anomaly("failure_spike", "a", "t1", "t2", 0.5, 0.0, 5.0, 6, 3),
            Anomaly("latency_spike", "a", "t1", "t2", 900.0, 100.0, 8.0, 6, 0),
            Anomaly("new_root_cause", "b", "t1", "t2", 1.0, 0.0, 0.0, 4, 1),
        ]
        summary = summarize_anomalies(anomalies)
        assert summary["total_anomalies"] == 3
        assert summary["by_type"] == {
            "failure_spike": 1, "latency_spike": 1, "new_root_cause": 1,
        }
        assert summary["functions_affected"] == 2
        assert summary["most_affected_function"] == "a"


class TestAnomaliesCLI:
    def _seed_store(self, db_path):
        store = EventStore(db_path)
        for event in steady_hours("api_call", hours=range(10, 15)):
            store.store_event(event)
        for i in range(6):
            store.store_event(make_event(
                "api_call", timestamp=hour_ts(15, minute=i * 5),
                success=i >= 4,
                root_cause="timeout" if i < 4 else None,
                duration_ms=100.0,
                event_id=f"spike-{i}",
            ))
        return store

    def _run(self, db_path, *extra):
        return subprocess.run(
            [sys.executable, "-m", "agent_sentry", "--db", db_path, "anomalies", *extra],
            capture_output=True, text=True,
        )

    def test_table_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db)
            assert result.returncode == 0
            assert "Anomalies" in result.stdout
            assert "api_call" in result.stdout
            assert "failure_spike" in result.stdout
            assert "Functions affected: 1" in result.stdout

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--json-output")
            assert result.returncode == 0
            payload = json.loads(result.stdout)
            assert payload["summary"]["total_anomalies"] >= 1
            types = {a["type"] for a in payload["anomalies"]}
            assert "failure_spike" in types

    def test_no_anomalies(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            store = EventStore(db)
            for event in steady_hours("steady", hours=range(10, 14)):
                store.store_event(event)
            result = self._run(db)
            assert result.returncode == 0
            assert "No anomalies detected" in result.stdout

    def test_bad_option_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--threshold", "0")
            assert result.returncode == 1
            assert "threshold must be positive" in result.stdout

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_sentry", "anomalies", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--bucket-minutes" in result.stdout
