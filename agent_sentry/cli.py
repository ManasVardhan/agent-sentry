"""CLI for agent-sentry."""

import argparse
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone

from .storage import get_store, DEFAULT_DB_PATH


def cmd_dashboard(args):
    """Launch the Streamlit dashboard."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    db_path = args.db or DEFAULT_DB_PATH
    cmd = [sys.executable, "-m", "streamlit", "run", dashboard_path, "--", db_path]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("Error: streamlit not found. Install with: pip install agent-sentry[dashboard]")
        sys.exit(1)


def cmd_report(args):
    """Print a terminal summary report."""
    store = get_store(args.db)

    hours = args.hours or 24
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    total = store.get_total_count(since)
    failures = store.get_failure_count(since)
    reliability = store.get_reliability_score(since)
    breakdown = store.get_failure_breakdown(since)
    type_breakdown = store.get_event_type_breakdown(since)

    print()
    print("=" * 50)
    print("  agent-sentry Report")
    print(f"  Period: Last {hours} hours")
    print("=" * 50)
    print()
    print(f"  Total Events:     {total}")
    print(f"  Failures:         {failures}")
    print(f"  Reliability:      {reliability}%")
    rate = (failures / total * 100) if total > 0 else 0
    print(f"  Failure Rate:     {rate:.1f}%")
    print()

    if breakdown:
        print("  Failures by Root Cause:")
        print("  " + "-" * 35)
        for cause, count in breakdown.items():
            bar = "#" * min(count, 30)
            print(f"    {cause:<20} {count:>4}  {bar}")
        print()

    if type_breakdown:
        print("  Events by Type:")
        print("  " + "-" * 35)
        for etype, counts in type_breakdown.items():
            total_t = counts["success"] + counts["failure"]
            print(f"    {etype:<20} {total_t:>4} ({counts['failure']} failed)")
        print()

    # Show recent failures
    recent = store.get_events(limit=5, success=False, since=since)
    if recent:
        print("  Recent Failures:")
        print("  " + "-" * 35)
        for event in recent:
            func = event.get("function_name", "unknown")
            cause = event.get("root_cause", "unknown")
            err = event.get("error_message", "")[:60]
            ts = event.get("timestamp", "")[:19]
            print(f"    [{ts}] {func}")
            print(f"      Cause: {cause} | {err}")
        print()

    print("=" * 50)
    print()


def cmd_clear(args):
    """Clear all stored events."""
    store = get_store(args.db)
    count = store.get_total_count()
    if count == 0:
        print("No events to clear.")
        return
    confirm = input(f"Clear {count} events? [y/N] ")
    if confirm.lower() == "y":
        store.clear()
        print(f"Cleared {count} events.")
    else:
        print("Cancelled.")


def main():
    parser = argparse.ArgumentParser(
        prog="agent-sentry",
        description="Crash reporting for AI agents.",
    )
    parser.add_argument("--db", help="Path to SQLite database", default=None)

    subparsers = parser.add_subparsers(dest="command")

    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Launch the Streamlit dashboard")
    dash_parser.set_defaults(func=cmd_dashboard)

    # report
    report_parser = subparsers.add_parser("report", help="Print a terminal summary report")
    report_parser.add_argument("--hours", type=int, default=24, help="Hours to look back (default: 24)")
    report_parser.set_defaults(func=cmd_report)

    # clear
    clear_parser = subparsers.add_parser("clear", help="Clear all stored events")
    clear_parser.set_defaults(func=cmd_clear)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
