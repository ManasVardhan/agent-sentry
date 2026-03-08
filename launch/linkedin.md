# LinkedIn Launch Post

I built crash reporting for AI agents.

Here's the problem: your agent works perfectly in demos. In production, it fails silently. Tool calls timeout. Context windows overflow. The LLM hallucinates a fake API endpoint, retries it 5 times, burns $2 in tokens, and returns garbage to the user.

Traditional monitoring tools see HTTP 200s and think everything is fine.

So I built agent-sentry. One Python decorator. It wraps your agent, catches every failure, and classifies the root cause automatically: timeout, hallucination, context overflow, malformed arguments, rate limit errors, auth failures.

You get a Streamlit dashboard with failure rates, cost tracking, and a reliability score you can monitor over time. Plus real-time alerts via Slack or webhooks when something breaks.

Three lines of code to add it. 60 tests. Works with OpenAI, Anthropic, and LangChain out of the box.

I've been contributing to HuggingFace Transformers, PyTorch, MLflow, and other ML repos for the past few months. This is the first tool I'm shipping from scratch based on patterns I kept seeing: agents failing in ways nobody was catching.

Open source, MIT licensed: https://github.com/ManasVardhan/agent-sentry

What's the worst silent failure you've seen from an AI agent in production?

#AI #OpenSource
