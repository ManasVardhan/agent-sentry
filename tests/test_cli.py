"""Tests for the CLI."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from io import StringIO

import pytest
from agent_sentry.storage import EventStore
from agent_sentry.cli import cmd_report


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
