# Roadmap

## v0.1.0 (Current)
- [x] Core `@watch` decorator
- [x] SQLite event storage
- [x] Root cause classification (timeout, hallucination, context overflow, malformed args, rate limit, auth error, silent failure)
- [x] Streamlit dashboard
- [x] Alert system (webhooks, Slack, email)
- [x] OpenAI integration
- [x] Anthropic integration
- [x] LangChain callback handler
- [x] CLI (dashboard, report, clear)

## v0.2.0
- [x] Async function support (`@watch` on async functions)
- [x] Event export (JSON, CSV) via `agent-sentry export` with format, output file, hours, event type, failures-only, and limit options
- [ ] CrewAI integration
- [ ] AutoGen integration
- [x] LlamaIndex integration via `AgentSentryLlamaIndexHandler` for LlamaIndex's callback manager: LLM calls with prompts, responses, and token usage, tool calls with input and result, queries and agent steps with duration, and exceptions captured as classified failures, with an optional `llamaindex` extra
- [x] Cost tracking analytics via `agent-sentry costs` with model, function, or day grouping, top-N, hours, and JSON output options, plus `aggregate_costs` and `summarize_costs` in the Python API and a Cost Tracking dashboard panel with total spend, wasted cost from failures, and spend-by-model and spend-by-day charts
- [x] Retry pattern detection via `agent-sentry retries` with window, min-attempts, hours, and JSON output options, plus `detect_retry_sequences` and `summarize_retries` in the Python API

## v0.3.0
- [ ] PostgreSQL storage backend
- [ ] Multi-agent session tracking
- [x] Failure correlation analysis via `agent-sentry correlate` with window, min-failures, min-co, hours, and JSON output options, plus `find_failure_clusters`, `correlate_failures`, and `summarize_correlations` in the Python API
- [x] Custom root cause classifiers via `register_classifier` with regex patterns and/or predicates, running ahead of built-in patterns, plus `unregister_classifier`, `list_classifiers`, and `clear_classifiers`
- [x] PagerDuty and Opsgenie alert channels via `PagerDutyAlert` (Events API v2, severity and source options, incident dedup by function and root cause) and `OpsgenieAlert` (Alert API, priority, tags, EU region, alert dedup by alias), plus `pagerduty_routing_key` and `opsgenie_api_key` shortcuts on `configure`
- [x] Prometheus metrics endpoint via `agent-sentry metrics` (print one scrape or `--serve` an HTTP endpoint on port 9464 with `--host`, `--port`, and `--limit` options), plus `build_metrics`, `create_metrics_server`, and `start_metrics_server` in the Python API, exporting reliability, event, failure, cost, and token metrics in the Prometheus text format

## v0.4.0
- [ ] Real-time streaming dashboard (WebSocket)
- [ ] Agent replay from captured events
- [ ] A/B testing for agent configurations
- [ ] Anomaly detection on failure patterns
- [ ] Team dashboard with auth

## v1.0.0
- [ ] Production-hardened storage
- [ ] Horizontal scaling support
- [ ] SDK for JavaScript/TypeScript agents
- [ ] SaaS hosted option
- [ ] SOC2 compliance features
