"""Tests for multi-agent session tracking."""

import asyncio
import json
import sqlite3

import pytest

from agent_sentry import watch
from agent_sentry.capture import EventCapture
from agent_sentry.sessions import (
    SessionSummary,
    current_agent,
    current_session,
    current_session_id,
    get_session_events,
    list_sessions,
    session,
    summarize_sessions,
)
from agent_sentry.storage import EventStore


@pytest.fixture
def store(tmp_path):
    return EventStore(db_path=str(tmp_path / "events.db"))


@pytest.fixture
def capture(store):
    class _NoAlerts:
        def send_alert(self, event):
            pass

    return EventCapture(store=store, alert_manager=_NoAlerts())


class TestSessionContext:
    def test_no_session_by_default(self):
        assert current_session() is None
        assert current_session_id() is None
        assert current_agent() is None

    def test_explicit_session_id_and_agent(self):
        with session("run-1", agent="planner"):
            assert current_session_id() == "run-1"
            assert current_agent() == "planner"
        assert current_session_id() is None

    def test_auto_generated_session_id(self):
        with session() as ctx:
            assert ctx.session_id.startswith("session-")
            assert current_session_id() == ctx.session_id

    def test_nested_session_inherits_id(self):
        with session("outer", agent="planner"):
            with session(agent="executor"):
                assert current_session_id() == "outer"
                assert current_agent() == "executor"
            assert current_agent() == "planner"

    def test_nested_explicit_id_overrides(self):
        with session("outer"):
            with session("inner"):
                assert current_session_id() == "inner"
            assert current_session_id() == "outer"

    def test_nested_inherits_agent_when_not_given(self):
        with session("outer", agent="planner"):
            with session():
                assert current_agent() == "planner"

    def test_context_restored_after_exception(self):
        with pytest.raises(ValueError):
            with session("boom"):
                raise ValueError("bad")
        assert current_session_id() is None


class TestCaptureStamping:
    def test_events_stamped_inside_session(self, store, capture):
        def agent_fn(x):
            return x * 2

        with session("run-42", agent="worker"):
            capture.capture_call(agent_fn, (3,), {})

        events = store.get_events()
        assert len(events) == 1
        assert events[0]["session_id"] == "run-42"
        assert events[0]["agent"] == "worker"

    def test_events_unstamped_outside_session(self, store, capture):
        capture.capture_call(lambda: "ok", (), {})
        events = store.get_events()
        assert events[0]["session_id"] is None
        assert events[0]["agent"] is None

    def test_failure_events_stamped(self, store, capture):
        def boom():
            raise RuntimeError("nope")

        with session("run-fail", agent="worker"):
            with pytest.raises(RuntimeError):
                capture.capture_call(boom, (), {})

        events = store.get_events(success=False)
        assert events[0]["session_id"] == "run-fail"

    def test_async_capture_stamped(self, store, capture):
        async def agent_fn():
            return "done"

        async def run():
            with session("run-async", agent="async-worker"):
                await capture.async_capture_call(agent_fn, (), {})

        asyncio.run(run())
        events = store.get_events()
        assert events[0]["session_id"] == "run-async"
        assert events[0]["agent"] == "async-worker"

    def test_log_event_stamped(self, store, capture):
        with session("run-log", agent="logger"):
            capture.log_event({"event_type": "custom"})
        events = store.get_events()
        assert events[0]["session_id"] == "run-log"
        assert events[0]["agent"] == "logger"

    def test_explicit_event_session_wins(self, store, capture):
        with session("ambient"):
            capture.log_event({"event_type": "custom", "session_id": "explicit"})
        events = store.get_events()
        assert events[0]["session_id"] == "explicit"

    def test_watch_decorator_stamps(self, store, capture):
        @watch(capture=capture)
        def my_agent(q):
            return q.upper()

        with session("run-watch", agent="searcher"):
            my_agent("hi")

        events = store.get_events()
        assert events[0]["session_id"] == "run-watch"
        assert events[0]["agent"] == "searcher"


class TestStorage:
    def test_get_events_filters_by_session_and_agent(self, store, capture):
        with session("s1", agent="a1"):
            capture.capture_call(lambda: 1, (), {})
        with session("s2", agent="a2"):
            capture.capture_call(lambda: 2, (), {})

        assert len(store.get_events(session_id="s1")) == 1
        assert len(store.get_events(agent="a2")) == 1
        assert store.get_events(session_id="s1")[0]["agent"] == "a1"

    def test_get_session_stats_aggregates(self, store, capture):
        with session("s1", agent="planner"):
            capture.capture_call(lambda: 1, (), {})
            with session(agent="executor"):
                with pytest.raises(RuntimeError):
                    capture.capture_call(
                        lambda: (_ for _ in ()).throw(RuntimeError("x")), (), {}
                    )

        rows = store.get_session_stats()
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "s1"
        assert row["total"] == 2
        assert row["failures"] == 1
        assert set((row["agents"] or "").split(",")) == {"planner", "executor"}

    def test_migration_adds_columns_to_old_db(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                function_name TEXT,
                args_json TEXT,
                result_json TEXT,
                error_message TEXT,
                error_type TEXT,
                traceback TEXT,
                duration_ms REAL,
                success INTEGER NOT NULL DEFAULT 1,
                token_usage_json TEXT,
                cost REAL,
                root_cause TEXT,
                metadata_json TEXT,
                tags TEXT
            )
        """)
        conn.execute(
            "INSERT INTO events (event_id, timestamp, event_type) "
            "VALUES ('e1', '2026-01-01T00:00:00+00:00', 'function_call')"
        )
        conn.commit()
        conn.close()

        store = EventStore(db_path=db_path)
        events = store.get_events()
        assert len(events) == 1
        assert events[0]["session_id"] is None

        store.store_event({
            "event_id": "e2",
            "event_type": "function_call",
            "session_id": "migrated",
            "agent": "worker",
        })
        assert store.get_events(session_id="migrated")[0]["agent"] == "worker"


class TestSessionsApi:
    def _seed(self, store, capture):
        with session("alpha", agent="planner"):
            capture.capture_call(lambda: 1, (), {})
            with session(agent="executor"):
                capture.capture_call(lambda: 2, (), {})
        with session("beta", agent="solo"):
            with pytest.raises(RuntimeError):
                capture.capture_call(
                    lambda: (_ for _ in ()).throw(RuntimeError("x")), (), {}
                )

    def test_list_sessions(self, store, capture):
        self._seed(store, capture)
        sessions = list_sessions(store)
        by_id = {s.session_id: s for s in sessions}
        assert set(by_id) == {"alpha", "beta"}
        assert by_id["alpha"].events == 2
        assert by_id["alpha"].failures == 0
        assert by_id["alpha"].agents == ["executor", "planner"]
        assert by_id["alpha"].reliability == 100.0
        assert by_id["beta"].failures == 1
        assert by_id["beta"].reliability == 0.0
        assert by_id["alpha"].duration_s is not None

    def test_get_session_events(self, store, capture):
        self._seed(store, capture)
        events = get_session_events("alpha", store=store)
        assert len(events) == 2
        assert all(e["session_id"] == "alpha" for e in events)

    def test_summarize_sessions(self, store, capture):
        self._seed(store, capture)
        totals = summarize_sessions(list_sessions(store))
        assert totals["total_sessions"] == 2
        assert totals["total_events"] == 3
        assert totals["total_failures"] == 1
        assert totals["multi_agent_sessions"] == 1

    def test_summarize_empty(self):
        totals = summarize_sessions([])
        assert totals["total_sessions"] == 0
        assert totals["reliability"] == 100.0

    def test_summary_to_dict(self):
        s = SessionSummary(session_id="x", events=4, failures=1)
        d = s.to_dict()
        assert d["reliability"] == 75.0
        assert d["session_id"] == "x"


class TestSessionsCli:
    def _run(self, argv, capsys):
        import sys as _sys

        from agent_sentry.cli import main

        old = _sys.argv
        _sys.argv = ["agent-sentry"] + argv
        try:
            main()
        finally:
            _sys.argv = old
        return capsys.readouterr().out

    def _seed_db(self, tmp_path):
        db = str(tmp_path / "cli.db")
        store = EventStore(db_path=db)

        class _NoAlerts:
            def send_alert(self, event):
                pass

        cap = EventCapture(store=store, alert_manager=_NoAlerts())
        with session("cli-session", agent="planner"):
            cap.capture_call(lambda: "ok", (), {})
            with session(agent="executor"):
                with pytest.raises(RuntimeError):
                    cap.capture_call(
                        lambda: (_ for _ in ()).throw(RuntimeError("x")), (), {}
                    )
        return db

    def test_sessions_list(self, tmp_path, capsys):
        db = self._seed_db(tmp_path)
        out = self._run(["--db", db, "sessions"], capsys)
        assert "cli-session" in out
        assert "executor, planner" in out
        assert "Multi-agent: 1" in out

    def test_sessions_list_json(self, tmp_path, capsys):
        db = self._seed_db(tmp_path)
        out = self._run(["--db", db, "sessions", "--json-output"], capsys)
        data = json.loads(out)
        assert data["summary"]["total_sessions"] == 1
        assert data["sessions"][0]["session_id"] == "cli-session"
        assert data["sessions"][0]["failures"] == 1

    def test_sessions_detail(self, tmp_path, capsys):
        db = self._seed_db(tmp_path)
        out = self._run(
            ["--db", db, "sessions", "--session-id", "cli-session"], capsys
        )
        assert "Session cli-session" in out
        assert "planner" in out
        assert "FAIL" in out

    def test_sessions_detail_json(self, tmp_path, capsys):
        db = self._seed_db(tmp_path)
        out = self._run(
            ["--db", db, "sessions", "--session-id", "cli-session", "--json-output"],
            capsys,
        )
        data = json.loads(out)
        assert data["session"]["events"] == 2
        assert len(data["events"]) == 2

    def test_sessions_unknown_id_exits(self, tmp_path, capsys):
        db = self._seed_db(tmp_path)
        with pytest.raises(SystemExit):
            self._run(["--db", db, "sessions", "--session-id", "nope"], capsys)

    def test_sessions_empty_db(self, tmp_path, capsys):
        db = str(tmp_path / "empty.db")
        EventStore(db_path=db)
        out = self._run(["--db", db, "sessions"], capsys)
        assert "No sessions recorded" in out
