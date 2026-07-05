"""Tests for the export storage method and 'export' CLI command."""

import csv
import io
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


def _make_event(
    name: str = "my_agent",
    success: bool = True,
    event_type: str = "function_call",
    offset_min: int = 0,
    token_usage=None,
    tags=None,
):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=offset_min)).isoformat()
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": ts,
        "event_type": event_type,
        "function_name": name,
        "success": success,
        "error_message": None if success else f"{name} failed",
        "error_type": None if success else "RuntimeError",
        "root_cause": None if success else "timeout",
        "duration_ms": 42.5,
        "token_usage": token_usage,
        "tags": tags,
    }


def _seed(store: EventStore):
    store.store_event(_make_event("alpha", success=True))
    store.store_event(_make_event("beta", success=False))
    store.store_event(
        _make_event(
            "gamma",
            success=True,
            event_type="llm_call",
            token_usage={"input": 10, "output": 20},
            tags=["prod", "v2"],
        )
    )


# Storage layer ----------------------------------------------------------------


def test_export_json_returns_all_events(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    data = json.loads(store.export_events(fmt="json"))
    assert len(data) == 3
    names = {e["function_name"] for e in data}
    assert names == {"alpha", "beta", "gamma"}


def test_export_json_includes_nested_fields(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    data = json.loads(store.export_events(fmt="json"))
    gamma = next(e for e in data if e["function_name"] == "gamma")
    assert gamma["token_usage"] == {"input": 10, "output": 20}
    assert gamma["tags"] == ["prod", "v2"]


def test_export_csv_header_and_rows(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    text = store.export_events(fmt="csv")
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    assert header[0] == "event_id"
    assert "function_name" in header
    assert "root_cause" in header
    assert len(body) == 3


def test_export_csv_serializes_nested_values_as_json(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    text = store.export_events(fmt="csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    gamma = next(r for r in rows if r["function_name"] == "gamma")
    assert json.loads(gamma["token_usage"]) == {"input": 10, "output": 20}
    assert json.loads(gamma["tags"]) == ["prod", "v2"]


def test_export_failures_only(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    data = json.loads(store.export_events(fmt="json", success=False))
    assert len(data) == 1
    assert data[0]["function_name"] == "beta"


def test_export_event_type_filter(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    data = json.loads(store.export_events(fmt="json", event_type="llm_call"))
    assert len(data) == 1
    assert data[0]["function_name"] == "gamma"


def test_export_since_filter(tmp_db):
    store = EventStore(db_path=tmp_db)
    store.store_event(_make_event("old", offset_min=120))
    store.store_event(_make_event("new", offset_min=1))
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    data = json.loads(store.export_events(fmt="json", since=since))
    assert [e["function_name"] for e in data] == ["new"]


def test_export_limit(tmp_db):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    data = json.loads(store.export_events(fmt="json", limit=2))
    assert len(data) == 2


def test_export_empty_store(tmp_db):
    store = EventStore(db_path=tmp_db)
    assert json.loads(store.export_events(fmt="json")) == []
    csv_rows = list(csv.reader(io.StringIO(store.export_events(fmt="csv"))))
    assert len(csv_rows) == 1  # header only


def test_export_invalid_format_raises(tmp_db):
    store = EventStore(db_path=tmp_db)
    with pytest.raises(ValueError, match="Unsupported export format"):
        store.export_events(fmt="xml")


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


def test_cli_export_json_stdout(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "export")
    assert code == 0
    data = json.loads(out)
    assert len(data) == 3


def test_cli_export_csv_stdout(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--format", "csv"
    )
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 4  # header + 3 events


def test_cli_export_to_file(tmp_db, monkeypatch, capsys, tmp_path):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    out_file = tmp_path / "events.json"
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "-o", str(out_file)
    )
    assert code == 0
    assert "Exported events to" in out
    data = json.loads(out_file.read_text())
    assert len(data) == 3


def test_cli_export_csv_to_file(tmp_db, monkeypatch, capsys, tmp_path):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    out_file = tmp_path / "events.csv"
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export",
        "--format", "csv", "-o", str(out_file),
    )
    assert code == 0
    rows = list(csv.reader(io.StringIO(out_file.read_text())))
    assert len(rows) == 4


def test_cli_export_failures_only(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--failures-only"
    )
    assert code == 0
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["function_name"] == "beta"


def test_cli_export_event_type(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--event-type", "llm_call"
    )
    assert code == 0
    data = json.loads(out)
    assert [e["function_name"] for e in data] == ["gamma"]


def test_cli_export_hours_filter(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    store.store_event(_make_event("old", offset_min=180))
    store.store_event(_make_event("new", offset_min=1))
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--hours", "1"
    )
    assert code == 0
    data = json.loads(out)
    assert [e["function_name"] for e in data] == ["new"]


def test_cli_export_limit(tmp_db, monkeypatch, capsys):
    store = EventStore(db_path=tmp_db)
    _seed(store)
    code, out, _ = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--limit", "1"
    )
    assert code == 0
    assert len(json.loads(out)) == 1


def test_cli_export_empty_db(tmp_db, monkeypatch, capsys):
    EventStore(db_path=tmp_db)
    code, out, _ = _run_cli(monkeypatch, capsys, "--db", tmp_db, "export")
    assert code == 0
    assert json.loads(out) == []


def test_cli_export_invalid_format_rejected(tmp_db, monkeypatch, capsys):
    code, _, err = _run_cli(
        monkeypatch, capsys, "--db", tmp_db, "export", "--format", "xml"
    )
    assert code != 0


# Subprocess sanity check -------------------------------------------------------


def test_export_help_subprocess():
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry", "export", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--format" in result.stdout
    assert "--failures-only" in result.stdout
