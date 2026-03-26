"""Extended CLI tests including version, status, and subprocess invocation."""

import subprocess
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from agent_sentry import __version__
from agent_sentry.storage import EventStore
from agent_sentry.cli import cmd_status, cmd_report, main


@pytest.fixture
def populated_store(tmp_path):
    store = EventStore(str(tmp_path / "test.db"))
    for i in range(15):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call" if i < 10 else "llm_call",
            "function_name": f"func_{i}",
            "success": i < 12,
            "error_message": "rate limit exceeded" if i >= 12 else None,
            "root_cause": "rate_limit" if i >= 12 else None,
            "duration_ms": 100.0 + i * 10,
        })
    return store


def test_version_flag():
    """The --version flag should print the version and exit."""
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["agent-sentry", "--version"]):
            main()
    assert exc_info.value.code == 0


def test_version_flag_short():
    """The -V flag should also work."""
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["agent-sentry", "-V"]):
            main()
    assert exc_info.value.code == 0


def test_status_command(populated_store, capsys):
    """Status should show version, DB path, total events, and reliability."""
    class Args:
        db = populated_store.db_path

    cmd_status(Args())
    captured = capsys.readouterr()
    assert __version__ in captured.out
    assert "Total events:" in captured.out
    assert "15" in captured.out
    assert "Reliability:" in captured.out
    assert "DB size:" in captured.out


def test_status_empty_db(tmp_path, capsys):
    """Status with empty DB should show 0 events and 100% reliability."""
    store = EventStore(str(tmp_path / "empty.db"))

    class Args:
        db = store.db_path

    cmd_status(Args())
    captured = capsys.readouterr()
    assert "Total events: 0" in captured.out
    assert "100.0%" in captured.out


def test_report_no_failures_section(tmp_path, capsys):
    """Report with only successes should not show 'Recent Failures' section."""
    store = EventStore(str(tmp_path / "success_only.db"))
    for i in range(5):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"ok_func_{i}",
            "success": True,
            "duration_ms": 50.0,
        })

    class Args:
        db = store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    assert "Total Events:     5" in captured.out
    assert "Failures:         0" in captured.out
    assert "Recent Failures" not in captured.out


def test_cli_report_via_subprocess(tmp_path):
    """Actually invoke agent-sentry report via subprocess."""
    store = EventStore(str(tmp_path / "sub.db"))
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "function_call",
        "function_name": "subprocess_test",
        "success": True,
        "duration_ms": 10.0,
    })

    # --db must come BEFORE the subcommand
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry.cli", "--db", store.db_path, "report"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "agent-sentry Report" in result.stdout
    assert "Total Events" in result.stdout


def test_cli_status_via_subprocess(tmp_path):
    """Actually invoke agent-sentry status via subprocess."""
    store = EventStore(str(tmp_path / "sub_status.db"))
    store.store_event({
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "function_call",
        "function_name": "status_test",
        "success": True,
    })

    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry.cli", "--db", store.db_path, "status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_cli_no_args_via_subprocess():
    """Running with no args should print help and exit 1."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry.cli"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1


def test_cli_version_via_subprocess():
    """Running with --version should print version string."""
    result = subprocess.run(
        [sys.executable, "-m", "agent_sentry.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_report_with_multiple_root_causes(tmp_path, capsys):
    """Report should show all distinct root causes in breakdown."""
    store = EventStore(str(tmp_path / "multi_rc.db"))
    causes = ["timeout", "rate_limit", "auth_error", "network_error", "malformed_args"]
    for i, cause in enumerate(causes):
        store.store_event({
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "function_call",
            "function_name": f"fail_{cause}",
            "success": False,
            "error_message": f"Error: {cause}",
            "root_cause": cause,
            "duration_ms": 100.0,
        })

    class Args:
        db = store.db_path
        hours = 24

    cmd_report(Args())
    captured = capsys.readouterr()
    for cause in causes:
        assert cause in captured.out
