"""Tests for the SQLite event storage."""

import uuid
import pytest
from datetime import datetime, timezone, timedelta

from agent_sentry.storage import EventStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_events.db")
    return EventStore(db_path)


@pytest.fixture
def sample_event():
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "function_call",
        "function_name": "test_func",
        "success": True,
        "duration_ms": 150.5,
    }


def test_store_and_retrieve_event(store, sample_event):
    store.store_event(sample_event)
    events = store.get_events(limit=10)
    assert len(events) == 1
    assert events[0]["event_id"] == sample_event["event_id"]
    assert events[0]["function_name"] == "test_func"


def test_store_failed_event(store):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "tool_call",
        "function_name": "search_tool",
        "success": False,
        "error_message": "Connection timeout",
        "error_type": "TimeoutError",
        "root_cause": "timeout",
        "duration_ms": 30500.0,
    }
    store.store_event(event)
    events = store.get_events(success=False)
    assert len(events) == 1
    assert events[0]["success"] is False
    assert events[0]["root_cause"] == "timeout"


def test_get_failure_count(store):
    for i in range(5):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"func_{i}",
            "success": i < 3,  # 3 successes, 2 failures
        })
    assert store.get_failure_count() == 2
    assert store.get_total_count() == 5


def test_reliability_score(store):
    for i in range(10):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"func_{i}",
            "success": i < 8,  # 80% success
        })
    assert store.get_reliability_score() == 80.0


def test_reliability_score_empty(store):
    assert store.get_reliability_score() == 100.0


def test_failure_breakdown(store):
    causes = ["timeout", "timeout", "rate_limit", "auth_error"]
    for i, cause in enumerate(causes):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"func_{i}",
            "success": False,
            "root_cause": cause,
        })
    breakdown = store.get_failure_breakdown()
    assert breakdown["timeout"] == 2
    assert breakdown["rate_limit"] == 1
    assert breakdown["auth_error"] == 1


def test_event_type_breakdown(store):
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "llm_call",
        "function_name": "gpt4",
        "success": True,
    })
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "llm_call",
        "function_name": "gpt4",
        "success": False,
    })
    breakdown = store.get_event_type_breakdown()
    assert breakdown["llm_call"]["success"] == 1
    assert breakdown["llm_call"]["failure"] == 1


def test_filter_by_since(store):
    old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    new_time = datetime.now(timezone.utc).isoformat()

    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": old_time,
        "event_type": "function_call",
        "function_name": "old_func",
        "success": True,
    })
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": new_time,
        "event_type": "function_call",
        "function_name": "new_func",
        "success": True,
    })

    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    events = store.get_events(since=since)
    assert len(events) == 1
    assert events[0]["function_name"] == "new_func"


def test_clear(store, sample_event):
    store.store_event(sample_event)
    assert store.get_total_count() == 1
    store.clear()
    assert store.get_total_count() == 0


def test_store_with_json_fields(store):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "llm_call",
        "function_name": "chat",
        "success": True,
        "args": {"model": "gpt-4", "messages": [{"role": "user"}]},
        "result": "Hello!",
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "metadata": {"session_id": "abc123"},
        "tags": ["openai", "chat"],
    }
    store.store_event(event)
    events = store.get_events()
    assert len(events) == 1
    assert events[0]["token_usage"]["prompt_tokens"] == 10
    assert "openai" in events[0]["tags"]
