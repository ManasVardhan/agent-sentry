"""Streamlit dashboard for agent-sentry failure analytics."""

import sys
import json
from datetime import datetime, timedelta, timezone

try:
    import streamlit as st
    import pandas as pd
except ImportError:
    print("Dashboard requires streamlit and pandas. Install with:")
    print("  pip install agent-sentry[dashboard]")
    sys.exit(1)

from ..storage import EventStore, DEFAULT_DB_PATH


def run_dashboard(db_path: str = DEFAULT_DB_PATH):
    """Launch the Streamlit dashboard."""
    st.set_page_config(
        page_title="agent-sentry Dashboard",
        page_icon="🔍",
        layout="wide",
    )

    st.title("🔍 agent-sentry Dashboard")
    st.caption("Crash reporting for AI agents")

    store = EventStore(db_path)

    # Time range selector
    col1, col2, col3 = st.columns(3)
    with col1:
        time_range = st.selectbox(
            "Time Range",
            ["Last Hour", "Last 24 Hours", "Last 7 Days", "Last 30 Days", "All Time"],
            index=1,
        )

    since = None
    if time_range != "All Time":
        hours_map = {
            "Last Hour": 1,
            "Last 24 Hours": 24,
            "Last 7 Days": 168,
            "Last 30 Days": 720,
        }
        hours = hours_map[time_range]
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Key metrics
    total = store.get_total_count(since)
    failures = store.get_failure_count(since)
    reliability = store.get_reliability_score(since)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Events", total)
    with col2:
        st.metric("Failures", failures)
    with col3:
        color = "🟢" if reliability >= 95 else "🟡" if reliability >= 80 else "🔴"
        st.metric("Reliability Score", f"{color} {reliability}%")
    with col4:
        rate = (failures / total * 100) if total > 0 else 0
        st.metric("Failure Rate", f"{rate:.1f}%")

    st.divider()

    # Failure breakdown by root cause
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Failures by Root Cause")
        breakdown = store.get_failure_breakdown(since)
        if breakdown:
            df = pd.DataFrame(
                list(breakdown.items()),
                columns=["Root Cause", "Count"],
            )
            st.bar_chart(df.set_index("Root Cause"))
        else:
            st.info("No failures recorded. Your agents are doing great!")

    with col2:
        st.subheader("Events by Type")
        type_breakdown = store.get_event_type_breakdown(since)
        if type_breakdown:
            rows = []
            for event_type, counts in type_breakdown.items():
                rows.append({
                    "Type": event_type,
                    "Success": counts["success"],
                    "Failure": counts["failure"],
                })
            df = pd.DataFrame(rows)
            st.bar_chart(df.set_index("Type"))
        else:
            st.info("No events recorded yet.")

    st.divider()

    # Failure rate over time
    st.subheader("Failure Rate Over Time")
    events = store.get_events(limit=1000, since=since)
    if events:
        for e in events:
            if isinstance(e.get("timestamp"), str):
                try:
                    e["time"] = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    e["time"] = datetime.now(timezone.utc)
            e["failed"] = 0 if e.get("success") else 1

        df = pd.DataFrame(events)
        if "time" in df.columns:
            df = df.set_index("time")
            # Resample by hour
            if len(df) > 0:
                hourly = df["failed"].resample("h").agg(["sum", "count"])
                hourly["failure_rate"] = (hourly["sum"] / hourly["count"] * 100).fillna(0)
                st.line_chart(hourly["failure_rate"])
    else:
        st.info("No events to chart.")

    st.divider()

    # Recent failures
    st.subheader("Recent Failures")
    recent_failures = store.get_events(limit=20, success=False, since=since)
    if recent_failures:
        for event in recent_failures:
            with st.expander(
                f"{'🔴'} {event.get('function_name', 'unknown')} "
                f"| {event.get('root_cause', 'unknown')} "
                f"| {event.get('timestamp', '')[:19]}"
            ):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.write(f"**Event Type:** {event.get('event_type')}")
                    st.write(f"**Duration:** {event.get('duration_ms', 0):.0f}ms")
                with col2:
                    st.write(f"**Error Type:** {event.get('error_type', 'N/A')}")
                    st.write(f"**Root Cause:** {event.get('root_cause', 'unknown')}")
                with col3:
                    st.write(f"**Event ID:** `{event.get('event_id', '')[:8]}...`")

                if event.get("error_message"):
                    st.error(f"**Error:** {event['error_message']}")
                if event.get("traceback"):
                    st.code(event["traceback"], language="python")
                if event.get("args"):
                    st.json(event["args"])
    else:
        st.success("No recent failures. Everything is running smoothly!")


def main():
    """Entry point for the dashboard."""
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    run_dashboard(db_path)


if __name__ == "__main__":
    main()
