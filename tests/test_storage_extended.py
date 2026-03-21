"""Extended storage tests: row_to_dict, bad JSON, large datasets, and query edge cases."""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest
from agent_sentry.storage import EventStore, get_store, reset_default_store


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "test.db"))


class TestRowToDictEdgeCases:
    def test_malformed_json_in_args(self, store):
        """Bad JSON in args_json should not crash, should return raw string."""
        # Insert raw bad JSON directly
        conn = sqlite3.connect(store.db_path)
        conn.execute(
            "INSERT INTO events (event_id, timestamp, event_type, success, args_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("bad-json-1", datetime.now(timezone.utc).isoformat(), "test", 1, "{broken json"),
        )
        conn.commit()
        conn.close()

        events = store.get_events()
        assert len(events) == 1
        # Should not crash, args should contain the raw string
        assert events[0]["args"] is not None or events[0].get("args_json") is not None

    def test_null_json_fields(self, store):
        """Null JSON fields should be handled gracefully."""
        store.store_event({
            "event_id": "null-json",
            "event_type": "test",
            "success": True,
            "args": None,
            "result": None,
            "token_usage": None,
            "metadata": None,
            "tags": None,
        })
        events = store.get_events()
        assert len(events) == 1
        assert events[0]["args"] is None
        assert events[0]["result"] is None
        assert events[0]["token_usage"] is None
        assert events[0]["metadata"] is None
        assert events[0]["tags"] is None

    def test_empty_string_json_fields(self, store):
        """Empty string JSON fields should be treated as None."""
        conn = sqlite3.connect(store.db_path)
        conn.execute(
            "INSERT INTO events (event_id, timestamp, event_type, success, args_json, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("empty-json", datetime.now(timezone.utc).isoformat(), "test", 1, "", ""),
        )
        conn.commit()
        conn.close()

        events = store.get_events()
        assert len(events) == 1
        # Empty strings should be treated as None by _row_to_dict
        assert events[0]["args"] is None or events[0]["args"] == ""


class TestQueryEdgeCases:
    def test_get_events_with_all_filters(self, store):
        """Combining all filters should work."""
        now = datetime.now(timezone.utc).isoformat()
        store.store_event({
            "event_id": "combo-1",
            "timestamp": now,
            "event_type": "llm_call",
            "success": False,
            "root_cause": "timeout",
        })
        store.store_event({
            "event_id": "combo-2",
            "timestamp": now,
            "event_type": "tool_call",
            "success": False,
            "root_cause": "rate_limit",
        })

        events = store.get_events(
            event_type="llm_call",
            success=False,
            root_cause="timeout",
            since=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        assert len(events) == 1
        assert events[0]["event_id"] == "combo-1"

    def test_get_events_no_match(self, store):
        """Querying with non-matching filters should return empty list."""
        store.store_event({
            "event_id": "no-match",
            "event_type": "test",
            "success": True,
        })
        events = store.get_events(event_type="nonexistent_type")
        assert events == []

    def test_get_events_offset_beyond_total(self, store):
        """Offset beyond total should return empty."""
        store.store_event({
            "event_id": "only-one",
            "event_type": "test",
            "success": True,
        })
        events = store.get_events(offset=100)
        assert events == []

    def test_failure_count_with_since_filter(self, store):
        """Failure count should respect the since filter."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        new_time = datetime.now(timezone.utc).isoformat()

        store.store_event({
            "event_id": "old-fail",
            "timestamp": old_time,
            "event_type": "test",
            "success": False,
        })
        store.store_event({
            "event_id": "new-fail",
            "timestamp": new_time,
            "event_type": "test",
            "success": False,
        })

        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert store.get_failure_count(since=since) == 1
        assert store.get_failure_count() == 2

    def test_failure_breakdown_with_since(self, store):
        """Failure breakdown should respect the since filter."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        new_time = datetime.now(timezone.utc).isoformat()

        store.store_event({
            "event_id": "old-timeout",
            "timestamp": old_time,
            "event_type": "test",
            "success": False,
            "root_cause": "timeout",
        })
        store.store_event({
            "event_id": "new-rate",
            "timestamp": new_time,
            "event_type": "test",
            "success": False,
            "root_cause": "rate_limit",
        })

        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        breakdown = store.get_failure_breakdown(since=since)
        assert "rate_limit" in breakdown
        assert "timeout" not in breakdown


class TestGetStoreFactory:
    def test_get_store_returns_same_instance(self):
        """get_store() without path should return the same global instance."""
        reset_default_store()
        try:
            s1 = get_store()
            s2 = get_store()
            assert s1 is s2
        finally:
            reset_default_store()

    def test_get_store_with_path_returns_new(self, tmp_path):
        """get_store(path) should always return a new instance."""
        s1 = get_store(str(tmp_path / "a.db"))
        s2 = get_store(str(tmp_path / "b.db"))
        assert s1 is not s2
        assert s1.db_path != s2.db_path


class TestLargeDataset:
    def test_store_1000_events(self, store):
        """Storing and querying 1000 events should work."""
        for i in range(1000):
            store.store_event({
                "event_id": f"bulk-{i}",
                "event_type": "function_call",
                "function_name": f"func_{i % 10}",
                "success": i % 5 != 0,  # 20% failures
                "root_cause": "timeout" if i % 5 == 0 else None,
                "duration_ms": float(i),
            })

        assert store.get_total_count() == 1000
        assert store.get_failure_count() == 200
        assert store.get_reliability_score() == 80.0

        breakdown = store.get_failure_breakdown()
        assert breakdown["timeout"] == 200
