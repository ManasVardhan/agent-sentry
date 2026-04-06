"""Tests for new 'top' and 'tail' CLI commands and supporting storage methods."""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from agent_sentry.storage import EventStore


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _make_event(name: str, success: bool, root_cause: str = "unknown", offset_min: int = 0):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=offset_min)).isoformat()
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": ts,
        "event_type": "function_call",
        "function_name": name,
        "success": success,
        "error_message": None if success else f"{name} failed",
        "error_type": None if success else "RuntimeError",
        "root_cause": None if success else root_cause,
        "duration_ms": 100.0,
    }


def _seed(store: EventStore):
    # alpha: 5 failures, 0 successes
    for _ in range(5):
        store.store_event(_make_event("alpha", success=False, root_cause="rate_limit"))
    # beta: 3 failures, 7 successes
    for _ in range(3):
        store.store_event(_make_event("beta", success=False, root_cause="timeout"))
    for _ in range(7):
        store.store_event(_make_event("beta", success=True))
    # gamma: 1 failure, 1 success
    store.store_event(_make_event("gamma", success=False, root_cause="unknown"))
    store.store_event(_make_event("gamma", success=True))
    # delta: all success (excluded from top)
    for _ in range(4):
        store.store_event(_make_event("delta", success=True))


# Storage layer ----------------------------------------------------------------


def test_get_top_failing_functions_basic(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    rows = store.get_top_failing_functions()
    names = [r["function_name"] for r in rows]
    assert names == ["alpha", "beta", "gamma"]
    alpha = rows[0]
    assert alpha["failures"] == 5
    assert alpha["total"] == 5
    assert alpha["failure_rate"] == 100.0
    beta = rows[1]
    assert beta["failures"] == 3
    assert beta["total"] == 10
    assert beta["failure_rate"] == 30.0


def test_get_top_failing_functions_excludes_clean_functions(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    rows = store.get_top_failing_functions()
    assert "delta" not in [r["function_name"] for r in rows]


def test_get_top_failing_functions_limit(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    rows = store.get_top_failing_functions(limit=2)
    assert len(rows) == 2
    assert rows[0]["function_name"] == "alpha"
    assert rows[1]["function_name"] == "beta"


def test_get_top_failing_functions_since(tmp_db):
    store = EventStore(db_path=tmp_db)
    # Old failure
    store.store_event(_make_event("old_func", success=False, offset_min=120))
    # Recent failure
    store.store_event(_make_event("new_func", success=False, offset_min=1))
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    rows = store.get_top_failing_functions(since=since)
    names = [r["function_name"] for r in rows]
    assert "new_func" in names
    assert "old_func" not in names


def test_get_top_failing_functions_empty(tmp_db):
    store = EventStore(db_path=tmp_db)
    rows = store.get_top_failing_functions()
    assert rows == []


def test_get_recent_failures_basic(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    failures = store.get_recent_failures(limit=5)
    assert len(failures) == 5
    assert all(not f["success"] for f in failures)


def test_get_recent_failures_since(tmp_db):
    store = EventStore(db_path=tmp_db)
    store.store_event(_make_event("old", success=False, offset_min=120))
    store.store_event(_make_event("new", success=False, offset_min=1))
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    failures = store.get_recent_failures(since=since)
    names = [f["function_name"] for f in failures]
    assert "new" in names
    assert "old" not in names


# CLI layer (in-process via main) ---------------------------------------------


def _run_cli(monkeypatch, capsys, *cli_args):
    from agent_sentry import cli as cli_mod
    monkeypatch.setattr(sys, "argv", ["agent-sentry", *cli_args])
    try:
        cli_mod.main()
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_cli_top_human_output(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "top")
    assert code in (None, 0)
    assert "Top Failing Functions" in out
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "delta" not in out


def test_cli_top_json_output(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "top", "--json-output")
    assert code in (None, 0)
    rows = json.loads(out.strip())
    assert isinstance(rows, list)
    assert rows[0]["function_name"] == "alpha"
    assert rows[0]["failures"] == 5


def test_cli_top_with_limit(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "top", "--limit", "1", "--json-output")
    rows = json.loads(out.strip())
    assert len(rows) == 1


def test_cli_top_hours_filter(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    store.store_event(_make_event("recent_fail", success=False, offset_min=5))
    store.store_event(_make_event("ancient_fail", success=False, offset_min=600))
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "top", "--hours", "1", "--json-output"
    )
    rows = json.loads(out.strip())
    names = [r["function_name"] for r in rows]
    assert "recent_fail" in names
    assert "ancient_fail" not in names


def test_cli_top_empty_store(tmp_db, monkeypatch, capsys):
    EventStore(db_path=tmp_db)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "top")
    assert "No failures recorded." in out


def test_cli_top_truncates_long_function_names(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    long_name = "this_is_a_very_long_function_name_that_should_truncate"
    store.store_event(_make_event(long_name, success=False))
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "top")
    assert "..." in out


def test_cli_tail_basic(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "tail")
    assert "Recent Failures" in out
    assert "alpha" in out or "beta" in out or "gamma" in out


def test_cli_tail_json_output(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "tail", "--json-output", "--limit", "3")
    events = json.loads(out.strip())
    assert isinstance(events, list)
    assert len(events) <= 3
    assert all(not e["success"] for e in events)


def test_cli_tail_empty(tmp_db, monkeypatch, capsys):
    EventStore(db_path=tmp_db)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "tail")
    assert "No failures recorded." in out


def test_cli_tail_hours_filter(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    store.store_event(_make_event("recent", success=False, offset_min=2))
    store.store_event(_make_event("ancient", success=False, offset_min=600))
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "tail", "--hours", "1", "--json-output"
    )
    events = json.loads(out.strip())
    names = [e["function_name"] for e in events]
    assert "recent" in names
    assert "ancient" not in names


# CLI subprocess smoke tests ---------------------------------------------------


def test_cli_top_subprocess(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry", "--db", tmp_db, "top", "--json-output"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    rows = json.loads(result.stdout.strip())
    assert rows[0]["function_name"] == "alpha"


def test_cli_tail_subprocess(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry", "--db", tmp_db, "tail", "--json-output"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    events = json.loads(result.stdout.strip())
    assert len(events) > 0
