"""Tests for retry pattern detection."""

import json
import subprocess
import sys
import tempfile

import pytest

from agent_sentry.retries import (
    RetrySequence,
    detect_retry_sequences,
    summarize_retries,
    _parse_ts,
)
from agent_sentry.storage import EventStore


def make_event(
    function_name="fetch_data",
    timestamp="2026-07-13T10:00:00+00:00",
    success=True,
    duration_ms=100.0,
    cost=None,
    root_cause=None,
    event_id=None,
):
    return {
        "event_id": event_id or f"{function_name}-{timestamp}-{success}",
        "function_name": function_name,
        "timestamp": timestamp,
        "success": success,
        "duration_ms": duration_ms,
        "cost": cost,
        "root_cause": root_cause,
        "event_type": "function_call",
    }


def ts(seconds):
    """Timestamp helper: seconds offset from a base time."""
    minute, sec = divmod(seconds, 60)
    return f"2026-07-13T10:{minute:02d}:{sec:02d}+00:00"


class TestParseTs:
    def test_parses_iso_with_offset(self):
        parsed = _parse_ts("2026-07-13T10:00:00+00:00")
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_parses_z_suffix(self):
        parsed = _parse_ts("2026-07-13T10:00:00Z")
        assert parsed is not None

    def test_naive_assumed_utc(self):
        parsed = _parse_ts("2026-07-13T10:00:00")
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_invalid_returns_none(self):
        assert _parse_ts("not a timestamp") is None

    def test_empty_and_non_string_return_none(self):
        assert _parse_ts("") is None
        assert _parse_ts(None) is None
        assert _parse_ts(12345) is None


class TestDetectRetrySequences:
    def test_empty_events(self):
        assert detect_retry_sequences([]) == []

    def test_all_successes_no_sequences(self):
        events = [make_event(timestamp=ts(i), success=True) for i in range(5)]
        assert detect_retry_sequences(events) == []

    def test_single_failure_not_a_sequence(self):
        events = [make_event(timestamp=ts(0), success=False)]
        assert detect_retry_sequences(events) == []

    def test_fail_then_success_recovers(self):
        events = [
            make_event(timestamp=ts(0), success=False, root_cause="timeout"),
            make_event(timestamp=ts(5), success=True),
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 1
        seq = seqs[0]
        assert seq.attempts == 2
        assert seq.failures == 1
        assert seq.recovered is True
        assert seq.outcome == "recovered"
        assert seq.root_causes == {"timeout": 1}

    def test_repeated_failures_exhausted(self):
        events = [
            make_event(timestamp=ts(i * 5), success=False, root_cause="rate_limit")
            for i in range(4)
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 1
        seq = seqs[0]
        assert seq.attempts == 4
        assert seq.failures == 4
        assert seq.recovered is False
        assert seq.outcome == "exhausted"
        assert seq.root_causes == {"rate_limit": 4}

    def test_gap_beyond_window_splits_sequences(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp=ts(5), success=False),
            # 120s gap, beyond the 60s window
            make_event(timestamp=ts(125), success=False),
            make_event(timestamp=ts(130), success=True),
        ]
        seqs = detect_retry_sequences(events, window_seconds=60)
        assert len(seqs) == 2
        assert seqs[0].attempts == 2
        assert seqs[0].recovered is False
        assert seqs[1].attempts == 2
        assert seqs[1].recovered is True

    def test_custom_window(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp=ts(10), success=True),
        ]
        assert len(detect_retry_sequences(events, window_seconds=5)) == 0
        assert len(detect_retry_sequences(events, window_seconds=15)) == 1

    def test_min_attempts_filter(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp=ts(5), success=True),
        ]
        assert len(detect_retry_sequences(events, min_attempts=3)) == 0
        events.insert(1, make_event(timestamp=ts(2), success=False, event_id="x"))
        assert len(detect_retry_sequences(events, min_attempts=3)) == 1

    def test_functions_grouped_independently(self):
        events = [
            make_event("func_a", timestamp=ts(0), success=False),
            make_event("func_b", timestamp=ts(1), success=False),
            make_event("func_a", timestamp=ts(2), success=True),
            make_event("func_b", timestamp=ts(3), success=False),
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 2
        by_name = {s.function_name: s for s in seqs}
        assert by_name["func_a"].recovered is True
        assert by_name["func_b"].recovered is False

    def test_success_closes_sequence_then_new_one_starts(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp=ts(5), success=True),
            make_event(timestamp=ts(10), success=False),
            make_event(timestamp=ts(15), success=False),
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 2
        assert seqs[0].recovered is True
        assert seqs[1].recovered is False

    def test_success_before_failure_not_counted(self):
        # A success followed by a failure is not a retry of the success
        events = [
            make_event(timestamp=ts(0), success=True),
            make_event(timestamp=ts(5), success=False),
        ]
        assert detect_retry_sequences(events) == []

    def test_events_without_function_name_skipped(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp=ts(5), success=True),
        ]
        events[0]["function_name"] = None
        assert detect_retry_sequences(events) == []

    def test_unparseable_timestamp_breaks_sequence(self):
        events = [
            make_event(timestamp=ts(0), success=False),
            make_event(timestamp="garbage", success=True, event_id="g"),
        ]
        assert detect_retry_sequences(events) == []

    def test_unsorted_input_is_sorted(self):
        events = [
            make_event(timestamp=ts(5), success=True),
            make_event(timestamp=ts(0), success=False),
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 1
        assert seqs[0].recovered is True

    def test_wasted_duration_and_cost(self):
        events = [
            make_event(timestamp=ts(0), success=False, duration_ms=500.0, cost=0.01),
            make_event(timestamp=ts(5), success=False, duration_ms=300.0, cost=0.02),
            make_event(timestamp=ts(10), success=True, duration_ms=200.0, cost=0.03),
        ]
        seqs = detect_retry_sequences(events)
        assert len(seqs) == 1
        seq = seqs[0]
        assert seq.total_duration_ms == pytest.approx(1000.0)
        assert seq.wasted_duration_ms == pytest.approx(800.0)
        assert seq.wasted_cost == pytest.approx(0.03)

    def test_none_duration_and_cost_treated_as_zero(self):
        events = [
            make_event(timestamp=ts(0), success=False, duration_ms=None, cost=None),
            make_event(timestamp=ts(5), success=True, duration_ms=None, cost=None),
        ]
        seqs = detect_retry_sequences(events)
        assert seqs[0].wasted_duration_ms == 0.0
        assert seqs[0].wasted_cost == 0.0

    def test_missing_root_cause_bucketed_unknown(self):
        events = [
            make_event(timestamp=ts(0), success=False, root_cause=None),
            make_event(timestamp=ts(5), success=True),
        ]
        seqs = detect_retry_sequences(events)
        assert seqs[0].root_causes == {"unknown": 1}

    def test_sequences_sorted_by_first_timestamp(self):
        events = [
            make_event("late_func", timestamp=ts(100), success=False),
            make_event("late_func", timestamp=ts(105), success=True),
            make_event("early_func", timestamp=ts(0), success=False),
            make_event("early_func", timestamp=ts(5), success=True),
        ]
        seqs = detect_retry_sequences(events)
        assert [s.function_name for s in seqs] == ["early_func", "late_func"]

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            detect_retry_sequences([], window_seconds=0)
        with pytest.raises(ValueError):
            detect_retry_sequences([], window_seconds=-1)

    def test_invalid_min_attempts_raises(self):
        with pytest.raises(ValueError):
            detect_retry_sequences([], min_attempts=1)

    def test_to_dict_roundtrips_via_json(self):
        events = [
            make_event(timestamp=ts(0), success=False, root_cause="timeout"),
            make_event(timestamp=ts(5), success=True),
        ]
        seq = detect_retry_sequences(events)[0]
        data = json.loads(json.dumps(seq.to_dict()))
        assert data["function_name"] == "fetch_data"
        assert data["outcome"] == "recovered"
        assert data["attempts"] == 2


class TestSummarizeRetries:
    def test_empty(self):
        summary = summarize_retries([])
        assert summary["total_sequences"] == 0
        assert summary["recovery_rate"] == 0.0
        assert summary["most_retried_function"] is None

    def test_mixed_outcomes(self):
        seqs = [
            RetrySequence("a", attempts=2, failures=1, recovered=True,
                          wasted_duration_ms=100.0, wasted_cost=0.01),
            RetrySequence("a", attempts=3, failures=3, recovered=False,
                          wasted_duration_ms=200.0, wasted_cost=0.02),
            RetrySequence("b", attempts=2, failures=1, recovered=True,
                          wasted_duration_ms=50.0, wasted_cost=0.0),
        ]
        summary = summarize_retries(seqs)
        assert summary["total_sequences"] == 3
        assert summary["recovered"] == 2
        assert summary["exhausted"] == 1
        assert summary["recovery_rate"] == pytest.approx(66.67)
        assert summary["total_wasted_duration_ms"] == pytest.approx(350.0)
        assert summary["total_wasted_cost"] == pytest.approx(0.03)
        assert summary["most_retried_function"] == "a"


class TestRetriesCLI:
    def _seed_store(self, db_path):
        store = EventStore(db_path)
        store.store_event(make_event(
            timestamp=ts(0), success=False, root_cause="timeout",
            duration_ms=500.0, event_id="e1",
        ))
        store.store_event(make_event(
            timestamp=ts(5), success=True, duration_ms=200.0, event_id="e2",
        ))
        store.store_event(make_event(
            "flaky_tool", timestamp=ts(20), success=False,
            root_cause="rate_limit", duration_ms=300.0, event_id="e3",
        ))
        store.store_event(make_event(
            "flaky_tool", timestamp=ts(25), success=False,
            root_cause="rate_limit", duration_ms=300.0, event_id="e4",
        ))
        return store

    def _run(self, db_path, *extra):
        return subprocess.run(
            [sys.executable, "-m", "agent_sentry", "--db", db_path, "retries", *extra],
            capture_output=True, text=True,
        )

    def test_table_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db)
            assert result.returncode == 0
            assert "Retry Patterns" in result.stdout
            assert "fetch_data" in result.stdout
            assert "flaky_tool" in result.stdout
            assert "recovered" in result.stdout
            assert "exhausted" in result.stdout
            assert "Recovery rate: 50.0%" in result.stdout

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--json-output")
            assert result.returncode == 0
            data = json.loads(result.stdout)
            assert data["summary"]["total_sequences"] == 2
            assert len(data["sequences"]) == 2

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            EventStore(db)
            result = self._run(db)
            assert result.returncode == 0
            assert "No retry patterns detected" in result.stdout

    def test_min_attempts_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--min-attempts", "3", "--json-output")
            data = json.loads(result.stdout)
            assert data["summary"]["total_sequences"] == 0

    def test_window_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            self._seed_store(db)
            result = self._run(db, "--window", "2", "--json-output")
            data = json.loads(result.stdout)
            assert data["summary"]["total_sequences"] == 0

    def test_invalid_window_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/events.db"
            EventStore(db)
            result = self._run(db, "--window", "0")
            assert result.returncode == 1
            assert "Error" in result.stdout

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent_sentry", "retries", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--window" in result.stdout
        assert "--min-attempts" in result.stdout
