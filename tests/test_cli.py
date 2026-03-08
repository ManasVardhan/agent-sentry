"""Tests for the CLI."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from agent_sentry.storage import EventStore
from agent_sentry.cli import cmd_report, cmd_clear, main


@pytest.fixture
def populated_store(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    for i in range(10):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"func_{i}",
            "success": i < 7,
            "error_message": "failed" if i >= 7 else None,
            "root_cause": "timeout" if i >= 7 else None,
            "duration_ms": 100.0 + i * 10,
        })
    return store


@pytest.fixture
def empty_store(tmp_path):
    return EventStore(str(tmp_path / "empty.db"))


def test_report_output(populated_store, capsys):
    class Args:
        db = populated_store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "agent-sentry Report" in captured.out
    assert "Total Events" in captured.out
    assert "Reliability" in captured.out
    assert "70.0%" in captured.out


def test_report_shows_failure_breakdown(populated_store, capsys):
    class Args:
        db = populated_store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Failures by Root Cause" in captured.out
    assert "timeout" in captured.out


def test_report_shows_recent_failures(populated_store, capsys):
    class Args:
        db = populated_store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Recent Failures" in captured.out


def test_report_empty_db(empty_store, capsys):
    class Args:
        db = empty_store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Total Events:     0" in captured.out
    assert "Reliability:      100.0%" in captured.out
    assert "Failure Rate:     0.0%" in captured.out


def test_report_custom_hours(populated_store, capsys):
    class Args:
        db = populated_store.db_path
        hours = 168

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Last 168 hours" in captured.out


def test_report_shows_event_type_breakdown(populated_store, capsys):
    class Args:
        db = populated_store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Events by Type" in captured.out
    assert "function_call" in captured.out


def test_clear_with_data(populated_store, capsys):
    class Args:
        db = populated_store.db_path

    with patch("builtins.input", return_value="y"):
        cmd_clear(Args())
    captured = capsys.readouterr()
    assert "Cleared 10 events" in captured.out

    # Verify events are gone
    assert populated_store.get_total_count() == 0


def test_clear_cancelled(populated_store, capsys):
    class Args:
        db = populated_store.db_path

    with patch("builtins.input", return_value="n"):
        cmd_clear(Args())
    captured = capsys.readouterr()
    assert "Cancelled" in captured.out

    # Events should still be there
    assert populated_store.get_total_count() == 10


def test_clear_empty_db(empty_store, capsys):
    class Args:
        db = empty_store.db_path

    cmd_clear(Args())
    captured = capsys.readouterr()
    assert "No events to clear" in captured.out


def test_main_no_command(capsys):
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["agent-sentry"]):
            main()
    assert exc_info.value.code == 1


def test_report_with_mixed_event_types(tmp_path, capsys):
    store = EventStore(str(tmp_path / "mixed.db"))
    for etype in ["function_call", "llm_call", "tool_call"]:
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": etype,
            "function_name": f"test_{etype}",
            "success": True,
            "duration_ms": 50.0,
        })
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "llm_call",
        "function_name": "failed_llm",
        "success": False,
        "error_message": "Rate limit exceeded",
        "root_cause": "rate_limit",
        "duration_ms": 200.0,
    })

    class Args:
        db = store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "llm_call" in captured.out
    assert "tool_call" in captured.out
    assert "function_call" in captured.out
