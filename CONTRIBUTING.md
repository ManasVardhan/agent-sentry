# Contributing to agent-sentry

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/ManasVardhan/agent-sentry.git
cd agent-sentry
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=agent_sentry --cov-report=term-missing
```

## Code Style

We use [ruff](https://github.com/astral-sh/ruff) for linting:

```bash
ruff check .
ruff format .
```

## Pull Request Process

1. Fork the repo and create your branch from `main`.
2. Write tests for any new functionality.
3. Make sure all tests pass.
4. Update the README if needed.
5. Open a PR with a clear description of your changes.

## What We're Looking For

- Bug fixes with regression tests
- New integrations (CrewAI, AutoGen, LlamaIndex, etc.)
- Dashboard improvements
- Performance optimizations
- Documentation improvements

## Reporting Issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your Python version and OS

## Code of Conduct

Be kind. Be constructive. We're all here to make AI agents more reliable.
