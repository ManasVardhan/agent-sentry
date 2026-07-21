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
- [ ] LlamaIndex integration
- [ ] Cost tracking dashboard panel
- [x] Retry pattern detection via `agent-sentry retries` with window, min-attempts, hours, and JSON output options, plus `detect_retry_sequences` and `summarize_retries` in the Python API

## v0.3.0
- [ ] PostgreSQL storage backend
- [ ] Multi-agent session tracking
- [ ] Failure correlation analysis
- [x] Custom root cause classifiers via `register_classifier` with regex patterns and/or predicates, running ahead of built-in patterns, plus `unregister_classifier`, `list_classifiers`, and `clear_classifiers`
- [ ] PagerDuty and Opsgenie alert channels
- [ ] Prometheus metrics endpoint

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
