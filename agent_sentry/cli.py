"""CLI for agent-sentry."""

import argparse
import json
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone

from . import __version__
from .storage import get_store, DEFAULT_DB_PATH


def cmd_dashboard(args):
    """Launch the Streamlit dashboard."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    if not os.path.exists(dashboard_path):
        print("Error: dashboard app not found. Reinstall agent-sentry.")
        sys.exit(1)
    db_path = args.db or DEFAULT_DB_PATH
    cmd = [sys.executable, "-m", "streamlit", "run", dashboard_path, "--", db_path]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("Error: streamlit not found. Install with: pip install agent-sentry[dashboard]")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


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


def cmd_status(args):
    """Show the current status of agent-sentry."""
    store = get_store(args.db)
    db_path = store.db_path
    total = store.get_total_count()
    failures = store.get_failure_count()
    reliability = store.get_reliability_score()

    print()
    print(f"  agent-sentry v{__version__}")
    print(f"  Database: {db_path}")
    print(f"  Total events: {total}")
    print(f"  Failures: {failures}")
    print(f"  Reliability: {reliability}%")
    db_exists = os.path.exists(db_path)
    if db_exists:
        size_bytes = os.path.getsize(db_path)
        if size_bytes < 1024:
            size_str = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
        print(f"  DB size: {size_str}")
    print()


def cmd_health(args):
    """Run a health check on the event store."""
    store = get_store(args.db)
    result = store.health_check()

    status_icon = "OK" if result["status"] == "healthy" else "FAIL"
    print()
    print(f"  agent-sentry Health Check: [{status_icon}]")
    print(f"  Status:       {result['status']}")
    print(f"  Database:     {result['db_path']}")
    print(f"  Events:       {result['event_count']}")

    size = result["db_size_bytes"]
    if size < 1024:
        size_str = f"{size} B"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size / (1024 * 1024):.1f} MB"
    print(f"  DB Size:      {size_str}")
    print(f"  Writable:     {'yes' if result['writable'] else 'no'}")

    if result.get("error"):
        print(f"  Error:        {result['error']}")
    print()

    sys.exit(0 if result["status"] == "healthy" else 1)


def cmd_summary(args):
    """Print a compact summary of the event store."""
    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    summary = store.get_summary(since)

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  agent-sentry Summary ({period})")
    print("  " + "-" * 40)
    print(f"  Events:        {summary['total_events']}")
    print(f"  Failures:      {summary['failures']}")
    print(f"  Reliability:   {summary['reliability_score']}%")
    if summary["avg_duration_ms"] is not None:
        print(f"  Avg Duration:  {summary['avg_duration_ms']}ms")

    if summary["top_root_causes"]:
        print()
        print("  Top Root Causes:")
        for cause, count in summary["top_root_causes"].items():
            print(f"    {cause:<20} {count}")

    if summary["event_types"]:
        print()
        print("  Event Types:")
        for etype, counts in summary["event_types"].items():
            total_t = counts["success"] + counts["failure"]
            print(f"    {etype:<20} {total_t} ({counts['failure']} failed)")
    print()


def cmd_top(args):
    """Print the top failing functions."""
    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    rows = store.get_top_failing_functions(limit=args.limit, since=since)

    if args.json_output:
        print(json.dumps(rows, indent=2))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Top Failing Functions ({period})")
    print("  " + "-" * 60)
    if not rows:
        print("  No failures recorded.")
        print()
        return

    print(f"  {'Function':<32} {'Fails':>6} {'Total':>6} {'Rate':>8}")
    print("  " + "-" * 60)
    for row in rows:
        name = row["function_name"]
        if len(name) > 32:
            name = name[:29] + "..."
        print(
            f"  {name:<32} {row['failures']:>6} {row['total']:>6} {row['failure_rate']:>7}%"
        )
    print()


def cmd_tail(args):
    """Print the most recent failed events."""
    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    events = store.get_recent_failures(limit=args.limit, since=since)

    if args.json_output:
        print(json.dumps(events, indent=2, default=str))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Recent Failures ({period})")
    print("  " + "-" * 60)
    if not events:
        print("  No failures recorded.")
        print()
        return

    for event in events:
        ts = (event.get("timestamp") or "")[:19]
        func = event.get("function_name") or "unknown"
        cause = event.get("root_cause") or "unknown"
        err = (event.get("error_message") or "")[:80]
        print(f"  [{ts}] {func}")
        print(f"    Cause: {cause}")
        if err:
            print(f"    Error: {err}")
    print()


def cmd_export(args):
    """Export events to JSON or CSV."""
    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    success = False if args.failures_only else None

    try:
        text = store.export_events(
            fmt=args.format,
            since=since,
            success=success,
            event_type=args.event_type,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"Exported events to {args.output} ({args.format})")
    else:
        print(text, end="" if args.format == "csv" else "\n")


def cmd_retries(args):
    """Detect and display retry patterns."""
    from .retries import detect_retry_sequences, summarize_retries

    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    events = store.get_events(limit=args.limit, since=since)

    try:
        sequences = detect_retry_sequences(
            events, window_seconds=args.window, min_attempts=args.min_attempts
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    summary = summarize_retries(sequences)

    if args.json_output:
        print(json.dumps(
            {"summary": summary, "sequences": [s.to_dict() for s in sequences]},
            indent=2,
        ))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Retry Patterns ({period}, window {args.window:g}s)")
    print("  " + "-" * 66)
    if not sequences:
        print("  No retry patterns detected.")
        print()
        return

    print(f"  {'Function':<28} {'Tries':>5} {'Fails':>5} {'Outcome':<10} {'Wasted':>9}")
    print("  " + "-" * 66)
    for seq in sequences:
        name = seq.function_name
        if len(name) > 28:
            name = name[:25] + "..."
        wasted = f"{seq.wasted_duration_ms / 1000:.1f}s"
        print(
            f"  {name:<28} {seq.attempts:>5} {seq.failures:>5} "
            f"{seq.outcome:<10} {wasted:>9}"
        )
        causes = ", ".join(f"{c} x{n}" for c, n in seq.root_causes.items())
        if causes:
            print(f"    Causes: {causes}")

    print()
    print(
        f"  Sequences: {summary['total_sequences']} | "
        f"Recovered: {summary['recovered']} | "
        f"Exhausted: {summary['exhausted']} | "
        f"Recovery rate: {summary['recovery_rate']}%"
    )
    wasted_s = summary["total_wasted_duration_ms"] / 1000
    line = f"  Wasted time: {wasted_s:.1f}s"
    if summary["total_wasted_cost"]:
        line += f" | Wasted cost: ${summary['total_wasted_cost']:.4f}"
    print(line)
    if summary["most_retried_function"]:
        print(f"  Most retried: {summary['most_retried_function']}")
    print()


def cmd_anomalies(args):
    """Detect and display failure-pattern anomalies."""
    from .anomalies import detect_anomalies, summarize_anomalies

    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    events = store.get_events(limit=args.limit, since=since)

    try:
        anomalies = detect_anomalies(
            events,
            bucket_minutes=args.bucket_minutes,
            threshold=args.threshold,
            min_events=args.min_events,
            min_buckets=args.min_buckets,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    summary = summarize_anomalies(anomalies)

    if args.json_output:
        print(json.dumps(
            {"summary": summary, "anomalies": [a.to_dict() for a in anomalies]},
            indent=2,
        ))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Anomalies ({period}, {args.bucket_minutes}m buckets, threshold {args.threshold:g})")
    print("  " + "-" * 74)
    if not anomalies:
        print("  No anomalies detected.")
        print()
        return

    print(f"  {'Function':<24} {'Type':<15} {'Observed':>10} {'Baseline':>10} {'Sigma':>6}")
    print("  " + "-" * 74)
    for anomaly in anomalies:
        name = anomaly.function_name
        if len(name) > 24:
            name = name[:21] + "..."
        if anomaly.type == "failure_spike":
            observed = f"{anomaly.observed * 100:.0f}%"
            baseline = f"{anomaly.baseline * 100:.0f}%"
            sigma = f"{anomaly.deviation:.1f}"
        elif anomaly.type == "latency_spike":
            observed = f"{anomaly.observed / 1000:.2f}s"
            baseline = f"{anomaly.baseline / 1000:.2f}s"
            sigma = f"{anomaly.deviation:.1f}"
        else:
            observed = f"x{anomaly.observed:.0f}"
            baseline = "-"
            sigma = "-"
        print(
            f"  {name:<24} {anomaly.type:<15} {observed:>10} {baseline:>10} {sigma:>6}"
        )
        detail = f"    At {anomaly.bucket_start} ({anomaly.events} events, {anomaly.failures} failures)"
        if anomaly.details.get("root_cause"):
            detail += f", new cause: {anomaly.details['root_cause']}"
        print(detail)

    print()
    types = ", ".join(f"{t}: {n}" for t, n in sorted(summary["by_type"].items()))
    print(
        f"  Anomalies: {summary['total_anomalies']} | "
        f"Functions affected: {summary['functions_affected']}"
    )
    if types:
        print(f"  By type: {types}")
    if summary["most_affected_function"]:
        print(f"  Most affected: {summary['most_affected_function']}")
    print()


def cmd_sessions(args):
    """List tracked sessions or show one session in detail."""
    from .sessions import get_session_events, list_sessions, summarize_sessions

    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    if args.session_id:
        sessions = [
            s for s in list_sessions(store, since=since, limit=args.limit)
            if s.session_id == args.session_id
        ]
        if not sessions:
            print(f"Error: no session found with id {args.session_id!r}")
            sys.exit(1)
        summary = sessions[0]
        events = get_session_events(args.session_id, store=store)

        if args.json_output:
            print(json.dumps(
                {"session": summary.to_dict(), "events": events},
                indent=2, default=str,
            ))
            return

        print()
        print(f"  Session {summary.session_id}")
        print("  " + "-" * 70)
        agents = ", ".join(summary.agents) if summary.agents else "(none)"
        print(f"  Agents: {agents}")
        print(
            f"  Events: {summary.events} | Failures: {summary.failures} | "
            f"Reliability: {summary.reliability}%"
        )
        if summary.duration_s is not None:
            print(f"  Duration: {summary.duration_s:.1f}s")
        if summary.total_cost:
            print(f"  Cost: ${summary.total_cost:.4f}")
        print()
        print(f"  {'Time':<10} {'Agent':<14} {'Function':<26} {'Status':<8}")
        print("  " + "-" * 70)
        for event in reversed(events):
            ts = (event.get("timestamp") or "")[11:19]
            agent = (event.get("agent") or "-")[:14]
            name = (event.get("function_name") or "-")
            if len(name) > 26:
                name = name[:23] + "..."
            status = "ok" if event.get("success") else "FAIL"
            line = f"  {ts:<10} {agent:<14} {name:<26} {status:<8}"
            if not event.get("success") and event.get("root_cause"):
                line += f" {event['root_cause']}"
            print(line)
        print()
        return

    sessions = list_sessions(store, since=since, limit=args.limit)

    if args.json_output:
        print(json.dumps(
            {
                "summary": summarize_sessions(sessions),
                "sessions": [s.to_dict() for s in sessions],
            },
            indent=2, default=str,
        ))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Sessions ({period})")
    print("  " + "-" * 74)
    if not sessions:
        print("  No sessions recorded. Wrap agent code in agent_sentry.session().")
        print()
        return

    print(
        f"  {'Session':<24} {'Agents':<18} {'Events':>6} {'Fails':>5} "
        f"{'Rel%':>6} {'Dur':>8}"
    )
    print("  " + "-" * 74)
    for s in sessions:
        sid = s.session_id
        if len(sid) > 24:
            sid = sid[:21] + "..."
        agents = ", ".join(s.agents) if s.agents else "-"
        if len(agents) > 18:
            agents = agents[:15] + "..."
        dur = f"{s.duration_s:.1f}s" if s.duration_s is not None else "-"
        print(
            f"  {sid:<24} {agents:<18} {s.events:>6} {s.failures:>5} "
            f"{s.reliability:>6.1f} {dur:>8}"
        )

    totals = summarize_sessions(sessions)
    print()
    line = (
        f"  Sessions: {totals['total_sessions']} | "
        f"Events: {totals['total_events']} | "
        f"Failures: {totals['total_failures']} | "
        f"Reliability: {totals['reliability']}%"
    )
    if totals["multi_agent_sessions"]:
        line += f" | Multi-agent: {totals['multi_agent_sessions']}"
    print(line)
    if totals["total_cost"]:
        print(f"  Total cost: ${totals['total_cost']:.4f}")
    print()


def cmd_costs(args):
    """Display cost tracking analytics."""
    from .costs import aggregate_costs, summarize_costs

    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    events = store.get_events(limit=args.limit, since=since)

    try:
        buckets = aggregate_costs(events, by=args.by)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    summary = summarize_costs(events)

    if args.json_output:
        print(json.dumps(
            {"summary": summary, "breakdown": [b.to_dict() for b in buckets[:args.top]]},
            indent=2,
        ))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Cost Tracking ({period}, by {args.by})")
    print("  " + "-" * 66)
    if not buckets:
        print("  No cost data recorded.")
        print("  Costs are captured automatically by the OpenAI and Anthropic")
        print("  integrations, or by events that include a 'cost' field.")
        print()
        return

    label = args.by.capitalize()
    print(f"  {label:<32} {'Calls':>6} {'Tokens':>9} {'Cost':>10} {'Wasted':>9}")
    print("  " + "-" * 66)
    for bucket in buckets[:args.top]:
        key = bucket.key
        if len(key) > 32:
            key = key[:29] + "..."
        cost_s = f"${bucket.cost:.4f}"
        wasted_s = f"${bucket.wasted_cost:.4f}"
        print(
            f"  {key:<32} {bucket.calls:>6} {bucket.tokens:>9} "
            f"{cost_s:>10} {wasted_s:>9}"
        )

    print()
    print(
        f"  Total: ${summary['total_cost']:.4f} across "
        f"{summary['tracked_calls']} tracked calls "
        f"({summary['total_tokens']} tokens)"
    )
    if summary["wasted_cost"]:
        print(
            f"  Wasted on failures: ${summary['wasted_cost']:.4f} "
            f"({summary['wasted_pct']}% of spend)"
        )
    if summary["top_model"]:
        print(f"  Top model: {summary['top_model']}")
    print()


def cmd_metrics(args):
    """Print or serve Prometheus metrics."""
    from .metrics import build_metrics, create_metrics_server

    store = get_store(args.db)

    if not args.serve:
        print(build_metrics(store, event_limit=args.limit), end="")
        return

    try:
        server = create_metrics_server(
            store, host=args.host, port=args.port, event_limit=args.limit
        )
    except OSError as exc:
        print(f"Error: could not bind {args.host}:{args.port}: {exc}")
        sys.exit(1)

    print(f"Serving Prometheus metrics on http://{args.host}:{args.port}/metrics")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMetrics server stopped.")
    finally:
        server.server_close()


def cmd_correlate(args):
    """Detect failure clusters and correlated function failures."""
    from .correlation import (
        correlate_failures,
        find_failure_clusters,
        summarize_correlations,
    )

    store = get_store(args.db)

    since = None
    if args.hours:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()

    events = store.get_events(limit=args.limit, since=since)

    try:
        clusters = find_failure_clusters(
            events, window_seconds=args.window, min_failures=args.min_failures
        )
        correlations = correlate_failures(clusters, min_co_occurrences=args.min_co)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    summary = summarize_correlations(clusters, correlations)

    if args.json_output:
        print(json.dumps(
            {
                "summary": summary,
                "clusters": [c.to_dict() for c in clusters],
                "correlations": [c.to_dict() for c in correlations],
            },
            indent=2,
        ))
        return

    period = f"last {args.hours}h" if args.hours else "all time"
    print()
    print(f"  Failure Correlation ({period}, window {args.window:g}s)")
    print("  " + "-" * 66)
    if not clusters:
        print("  No failure clusters detected.")
        print()
        return

    print(f"  {'Start':<20} {'Fails':>5} {'Span':>7}  Functions")
    print("  " + "-" * 66)
    for cluster in clusters:
        start = cluster.start_timestamp[:19]
        span = f"{cluster.span_seconds:.1f}s"
        funcs = ", ".join(f"{n} x{c}" for n, c in sorted(cluster.functions.items()))
        if len(funcs) > 34:
            funcs = funcs[:31] + "..."
        print(f"  {start:<20} {cluster.failures:>5} {span:>7}  {funcs}")
        causes = ", ".join(f"{c} x{n}" for c, n in sorted(cluster.root_causes.items()))
        if causes:
            print(f"    Causes: {causes}")

    if correlations:
        print()
        print("  Correlated Function Pairs:")
        print("  " + "-" * 66)
        print(f"  {'Pair':<44} {'Together':>8} {'Score':>7}")
        for corr in correlations:
            pair = f"{corr.function_a} + {corr.function_b}"
            if len(pair) > 44:
                pair = pair[:41] + "..."
            print(f"  {pair:<44} {corr.co_occurrences:>8} {corr.score:>7.2f}")

    print()
    print(
        f"  Clusters: {summary['total_clusters']} | "
        f"Multi-function: {summary['multi_function_clusters']} | "
        f"Largest: {summary['largest_cluster']} failures"
    )
    if summary["most_involved_function"]:
        print(f"  Most involved: {summary['most_involved_function']}")
    if summary["top_pair"]:
        print(f"  Top pair: {summary['top_pair']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="agent-sentry",
        description="Crash reporting for AI agents.",
    )
    parser.add_argument("--db", help="Path to SQLite database", default=None)
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"agent-sentry {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Launch the Streamlit dashboard")
    dash_parser.set_defaults(func=cmd_dashboard)

    # report
    report_parser = subparsers.add_parser("report", help="Print a terminal summary report")
    report_parser.add_argument(
        "--hours", type=int, default=24,
        help="Hours to look back (default: 24)",
    )
    report_parser.set_defaults(func=cmd_report)

    # clear
    clear_parser = subparsers.add_parser("clear", help="Clear all stored events")
    clear_parser.set_defaults(func=cmd_clear)

    # status
    status_parser = subparsers.add_parser("status", help="Show agent-sentry status and DB info")
    status_parser.set_defaults(func=cmd_status)

    # health
    health_parser = subparsers.add_parser("health", help="Run a health check on the event store")
    health_parser.set_defaults(func=cmd_health)

    # summary
    summary_parser = subparsers.add_parser("summary", help="Print a compact event summary")
    summary_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    summary_parser.set_defaults(func=cmd_summary)

    # top (most failing functions)
    top_parser = subparsers.add_parser(
        "top", help="Show top failing functions ranked by failure count"
    )
    top_parser.add_argument(
        "--limit", type=int, default=10,
        help="Maximum number of rows to display (default: 10)",
    )
    top_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    top_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    top_parser.set_defaults(func=cmd_top)

    # tail (recent failures)
    tail_parser = subparsers.add_parser(
        "tail", help="Show the most recent failed events"
    )
    tail_parser.add_argument(
        "--limit", type=int, default=10,
        help="Maximum number of failures to display (default: 10)",
    )
    tail_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    tail_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of human-readable output",
    )
    tail_parser.set_defaults(func=cmd_tail)

    # export (JSON/CSV dump of events)
    export_parser = subparsers.add_parser(
        "export", help="Export events to JSON or CSV"
    )
    export_parser.add_argument(
        "--format", choices=["json", "csv"], default="json",
        help="Export format (default: json)",
    )
    export_parser.add_argument(
        "--output", "-o", default=None,
        help="Output file (prints to stdout if omitted)",
    )
    export_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    export_parser.add_argument(
        "--event-type", default=None,
        help="Filter by event type (e.g. llm_call, tool_call)",
    )
    export_parser.add_argument(
        "--failures-only", action="store_true",
        help="Export only failed events",
    )
    export_parser.add_argument(
        "--limit", type=int, default=10000,
        help="Maximum number of events to export (default: 10000)",
    )
    export_parser.set_defaults(func=cmd_export)

    # retries (retry pattern detection)
    retries_parser = subparsers.add_parser(
        "retries", help="Detect retry patterns (repeated failing calls)"
    )
    retries_parser.add_argument(
        "--window", type=float, default=60.0,
        help="Max seconds between attempts in a sequence (default: 60)",
    )
    retries_parser.add_argument(
        "--min-attempts", type=int, default=2,
        help="Minimum calls to count as a retry sequence (default: 2)",
    )
    retries_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    retries_parser.add_argument(
        "--limit", type=int, default=10000,
        help="Maximum number of events to analyze (default: 10000)",
    )
    retries_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    retries_parser.set_defaults(func=cmd_retries)

    # anomalies (failure pattern anomaly detection)
    anomalies_parser = subparsers.add_parser(
        "anomalies", help="Detect failure rate spikes, latency spikes, and new root causes"
    )
    anomalies_parser.add_argument(
        "--bucket-minutes", type=int, default=60,
        help="Time bucket width in minutes (default: 60)",
    )
    anomalies_parser.add_argument(
        "--threshold", type=float, default=3.0,
        help="Standard deviations above baseline to flag (default: 3.0)",
    )
    anomalies_parser.add_argument(
        "--min-events", type=int, default=5,
        help="Minimum events in a bucket to evaluate spikes (default: 5)",
    )
    anomalies_parser.add_argument(
        "--min-buckets", type=int, default=3,
        help="Minimum buckets per function for spike detection (default: 3)",
    )
    anomalies_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    anomalies_parser.add_argument(
        "--limit", type=int, default=10000,
        help="Maximum number of events to analyze (default: 10000)",
    )
    anomalies_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    anomalies_parser.set_defaults(func=cmd_anomalies)

    # correlate (failure correlation analysis)
    correlate_parser = subparsers.add_parser(
        "correlate", help="Detect failure clusters and correlated function failures"
    )
    correlate_parser.add_argument(
        "--window", type=float, default=30.0,
        help="Max seconds between failures in a cluster (default: 30)",
    )
    correlate_parser.add_argument(
        "--min-failures", type=int, default=2,
        help="Minimum failures for a cluster to count (default: 2)",
    )
    correlate_parser.add_argument(
        "--min-co", type=int, default=2,
        help="Minimum shared clusters to report a function pair (default: 2)",
    )
    correlate_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    correlate_parser.add_argument(
        "--limit", type=int, default=10000,
        help="Maximum number of events to analyze (default: 10000)",
    )
    correlate_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    correlate_parser.set_defaults(func=cmd_correlate)

    # sessions (multi-agent session tracking)
    sessions_parser = subparsers.add_parser(
        "sessions", help="List tracked sessions or inspect one session"
    )
    sessions_parser.add_argument(
        "--session-id", default=None,
        help="Show a detailed event timeline for one session",
    )
    sessions_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    sessions_parser.add_argument(
        "--limit", type=int, default=50,
        help="Maximum number of sessions to list (default: 50)",
    )
    sessions_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    sessions_parser.set_defaults(func=cmd_sessions)

    # costs (spend analytics)
    costs_parser = subparsers.add_parser(
        "costs", help="Show LLM spend breakdown by model, function, or day"
    )
    costs_parser.add_argument(
        "--by", choices=["model", "function", "day"], default="model",
        help="Grouping key for the breakdown (default: model)",
    )
    costs_parser.add_argument(
        "--top", type=int, default=10,
        help="Maximum number of rows to display (default: 10)",
    )
    costs_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours to look back (default: all time)",
    )
    costs_parser.add_argument(
        "--limit", type=int, default=10000,
        help="Maximum number of events to analyze (default: 10000)",
    )
    costs_parser.add_argument(
        "--json-output", action="store_true",
        help="Emit JSON instead of a formatted table",
    )
    costs_parser.set_defaults(func=cmd_costs)

    # metrics (Prometheus exposition)
    metrics_parser = subparsers.add_parser(
        "metrics", help="Print or serve Prometheus metrics"
    )
    metrics_parser.add_argument(
        "--serve", action="store_true",
        help="Serve metrics over HTTP instead of printing one scrape",
    )
    metrics_parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind the metrics server to (default: 127.0.0.1)",
    )
    metrics_parser.add_argument(
        "--port", type=int, default=9464,
        help="Port for the metrics server (default: 9464)",
    )
    metrics_parser.add_argument(
        "--limit", type=int, default=100000,
        help="Maximum events scanned for cost metrics (default: 100000)",
    )
    metrics_parser.set_defaults(func=cmd_metrics)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
