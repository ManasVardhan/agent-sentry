# Reddit Posts

## r/MachineLearning
**Title:** [P] agent-sentry: Crash reporting for AI agents (open source)

I built a Python library that adds crash reporting to AI agents. One decorator wraps your agent, catches every failure, classifies the root cause (timeout, hallucination, context overflow, malformed args, rate limit, auth error), and gives you a dashboard with reliability metrics.

Quick start is three lines of code. Works with OpenAI, Anthropic, and LangChain.

Built it because I kept deploying agents that failed silently. Monitoring showed 200s but the agent was hallucinating API endpoints and burning tokens. Figured others probably had the same problem.

60 tests, MIT licensed: https://github.com/ManasVardhan/agent-sentry

## r/LocalLLaMA
**Title:** agent-sentry: open source crash reporting for LLM agents

Built a tool that wraps your LLM agent with one decorator and catches silent failures. Classifies root causes automatically, tracks token costs, and gives you a reliability score.

Basically Sentry but for agents. Works with any LLM provider.

https://github.com/ManasVardhan/agent-sentry

## r/artificial
**Title:** I built crash reporting for AI agents because mine kept failing silently

Same content as r/MachineLearning post.
