"""Multi-agent session tracking for agent-sentry.

A session groups events from multiple agents (or multiple runs of one agent)
under a shared session_id. Enter a session with the session() context manager
and every event captured inside it, by @watch, integrations, or log_event,
is stamped with the session_id and the current agent name.

Example:

    from agent_sentry import watch, session

    @watch
    def plan(task):
        ...

    @watch
    def execute(step):
        ...

    with session("ticket-4812", agent="planner"):
        steps = plan("refund the customer")
        with session(agent="executor"):
            for step in steps:
                execute(step)

Both agents' events share session_id "ticket-4812" and carry their own
agent name, so the whole workflow can be inspected as one unit with
`agent-sentry sessions`.
"""

import contextvars
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .storage import EventStore, get_store

_session_ctx: "contextvars.ContextVar[Optional[Dict[str, Optional[str]]]]" = (
    contextvars.ContextVar("agent_sentry_session", default=None)
)


class SessionContext:
    """Context manager that scopes captured events to a session.

    Nested sessions inherit the parent session_id unless an explicit one is
    given, so sub-agents can switch their agent name while staying in the
    same session.
    """

    def __init__(self, session_id: Optional[str] = None, agent: Optional[str] = None):
        parent = _session_ctx.get()
        if session_id is None and parent is not None:
            session_id = parent.get("session_id")
        self.session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
        if agent is None and parent is not None:
            agent = parent.get("agent")
        self.agent = agent
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> "SessionContext":
        self._token = _session_ctx.set(
            {"session_id": self.session_id, "agent": self.agent}
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _session_ctx.reset(self._token)
            self._token = None


def session(
    session_id: Optional[str] = None, agent: Optional[str] = None
) -> SessionContext:
    """Open a session scope for event capture.

    Args:
        session_id: Shared identifier for the session. Auto-generated when
            omitted, or inherited from an enclosing session.
        agent: Name of the agent doing work inside this scope.

    Returns:
        A SessionContext usable as a context manager.
    """
    return SessionContext(session_id=session_id, agent=agent)


def current_session() -> Optional[Dict[str, Optional[str]]]:
    """Return the active session info ({session_id, agent}) or None."""
    active = _session_ctx.get()
    return dict(active) if active is not None else None


def current_session_id() -> Optional[str]:
    """Return the active session_id, or None outside any session."""
    active = _session_ctx.get()
    return active.get("session_id") if active is not None else None


def current_agent() -> Optional[str]:
    """Return the active agent name, or None if not set."""
    active = _session_ctx.get()
    return active.get("agent") if active is not None else None


@dataclass
class SessionSummary:
    """Aggregated view of one tracked session."""

    session_id: str
    events: int
    failures: int
    agents: List[str] = field(default_factory=list)
    started: Optional[str] = None
    ended: Optional[str] = None
    duration_s: Optional[float] = None
    total_cost: Optional[float] = None

    @property
    def reliability(self) -> float:
        """Success rate for the session as a 0-100 score."""
        if self.events == 0:
            return 100.0
        return round((1 - self.failures / self.events) * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "events": self.events,
            "failures": self.failures,
            "reliability": self.reliability,
            "agents": self.agents,
            "started": self.started,
            "ended": self.ended,
            "duration_s": self.duration_s,
            "total_cost": self.total_cost,
        }


def _span_seconds(started: Optional[str], ended: Optional[str]) -> Optional[float]:
    if not started or not ended:
        return None
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = datetime.fromisoformat(ended)
    except ValueError:
        return None
    return round((end_dt - start_dt).total_seconds(), 3)


def list_sessions(
    store: Optional[EventStore] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> List[SessionSummary]:
    """List tracked sessions, most recent first.

    Args:
        store: EventStore to query (default store when omitted).
        since: Optional ISO timestamp lower bound.
        limit: Maximum number of sessions to return.

    Returns:
        SessionSummary objects aggregated per session_id.
    """
    store = store or get_store()
    rows = store.get_session_stats(since=since, limit=limit)
    summaries: List[SessionSummary] = []
    for row in rows:
        agents = sorted(a for a in (row.get("agents") or "").split(",") if a)
        summaries.append(
            SessionSummary(
                session_id=row["session_id"],
                events=int(row.get("total") or 0),
                failures=int(row.get("failures") or 0),
                agents=agents,
                started=row.get("started"),
                ended=row.get("ended"),
                duration_s=_span_seconds(row.get("started"), row.get("ended")),
                total_cost=row.get("total_cost"),
            )
        )
    return summaries


def get_session_events(
    session_id: str,
    store: Optional[EventStore] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Return all stored events for one session, newest first."""
    store = store or get_store()
    return store.get_events(limit=limit, session_id=session_id)


def summarize_sessions(sessions: List[SessionSummary]) -> Dict[str, Any]:
    """Aggregate statistics across a list of session summaries."""
    total_events = sum(s.events for s in sessions)
    total_failures = sum(s.failures for s in sessions)
    reliability = 100.0
    if total_events:
        reliability = round((1 - total_failures / total_events) * 100, 2)
    costs = [s.total_cost for s in sessions if s.total_cost]
    return {
        "total_sessions": len(sessions),
        "total_events": total_events,
        "total_failures": total_failures,
        "reliability": reliability,
        "multi_agent_sessions": sum(1 for s in sessions if len(s.agents) > 1),
        "total_cost": round(sum(costs), 6) if costs else None,
    }
