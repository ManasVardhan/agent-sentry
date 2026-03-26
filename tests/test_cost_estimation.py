"""Tests for cost estimation in OpenAI and Anthropic wrappers."""

import pytest
from agent_sentry.integrations.openai import _estimate_cost as openai_cost, _COST_TABLE as OAI_TABLE
from agent_sentry.integrations.anthropic import _estimate_cost as anthropic_cost, _COST_TABLE as ANT_TABLE


class TestOpenAICostEstimation:
    def test_gpt4_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("gpt-4", usage)
        assert cost is not None
        # gpt-4: per the actual cost table
        rates = OAI_TABLE["gpt-4"]
        expected = (1000 / 1000 * rates["prompt"]) + (500 / 1000 * rates["completion"])
        assert cost == round(expected, 6)

    def test_gpt4_turbo_cost(self):
        usage = {"prompt_tokens": 2000, "completion_tokens": 1000}
        cost = openai_cost("gpt-4-turbo-preview", usage)
        assert cost is not None
        assert cost > 0

    def test_gpt4o_cost(self):
        """gpt-4o should match gpt-4o pricing, not gpt-4."""
        usage = {"prompt_tokens": 500, "completion_tokens": 200}
        cost = openai_cost("gpt-4o", usage)
        assert cost is not None
        # Should use gpt-4o rates specifically
        rates = OAI_TABLE["gpt-4o"]
        expected = (500 / 1000 * rates["prompt"]) + (200 / 1000 * rates["completion"])
        assert cost == round(expected, 6)

    def test_gpt4o_mini_cost(self):
        """gpt-4o-mini should match gpt-4o-mini pricing, not gpt-4o."""
        usage = {"prompt_tokens": 10000, "completion_tokens": 5000}
        cost = openai_cost("gpt-4o-mini", usage)
        assert cost is not None
        rates = OAI_TABLE["gpt-4o-mini"]
        expected = (10000 / 1000 * rates["prompt"]) + (5000 / 1000 * rates["completion"])
        assert cost == round(expected, 6)

    def test_gpt4o_mini_before_gpt4o(self):
        """gpt-4o-mini must not be priced as gpt-4o (prefix ordering matters)."""
        mini_cost = openai_cost("gpt-4o-mini", {"prompt_tokens": 1000, "completion_tokens": 1000})
        full_cost = openai_cost("gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 1000})
        assert mini_cost is not None
        assert full_cost is not None
        assert mini_cost < full_cost  # mini should be cheaper

    def test_gpt35_turbo_cost(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        cost = openai_cost("gpt-3.5-turbo", usage)
        assert cost is not None
        rates = OAI_TABLE["gpt-3.5-turbo"]
        expected = (1000 / 1000 * rates["prompt"]) + (500 / 1000 * rates["completion"])
        assert cost == round(expected, 6)

    def test_unknown_model_returns_none(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        assert openai_cost("llama-3-70b", usage) is None

    def test_zero_tokens(self):
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        cost = openai_cost("gpt-4", usage)
        assert cost == 0.0

    def test_missing_token_keys(self):
        """Missing keys should default to 0."""
        cost = openai_cost("gpt-4", {})
        assert cost == 0.0

    def test_partial_model_match(self):
        """Model name containing a known prefix should still match."""
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        cost = openai_cost("gpt-4-0613", usage)
        assert cost is not None
        assert cost > 0

    def test_all_known_models_have_costs(self):
        """Every model key in the cost table should produce a valid cost."""
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        for model_key in OAI_TABLE:
            cost = openai_cost(model_key, usage)
            assert cost is not None, f"No cost for {model_key}"
            assert cost >= 0, f"Negative cost for {model_key}"


class TestAnthropicCostEstimation:
    def test_claude3_opus_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-opus-20240229", usage)
        assert cost is not None
        rates = ANT_TABLE["claude-3-opus"]
        expected = (1000 / 1000 * rates["input"]) + (500 / 1000 * rates["output"])
        assert cost == round(expected, 6)

    def test_claude3_sonnet_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-sonnet-20240229", usage)
        assert cost is not None
        rates = ANT_TABLE["claude-3-sonnet"]
        expected = (1000 / 1000 * rates["input"]) + (500 / 1000 * rates["output"])
        assert cost == round(expected, 6)

    def test_claude3_haiku_cost(self):
        usage = {"input_tokens": 5000, "output_tokens": 2000}
        cost = anthropic_cost("claude-3-haiku-20240307", usage)
        assert cost is not None
        rates = ANT_TABLE["claude-3-haiku"]
        expected = (5000 / 1000 * rates["input"]) + (2000 / 1000 * rates["output"])
        assert cost == round(expected, 6)

    def test_claude_sonnet_4_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-sonnet-4-20250514", usage)
        assert cost is not None
        assert cost > 0

    def test_claude35_sonnet_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-5-sonnet-20241022", usage)
        assert cost is not None
        assert cost > 0

    def test_claude35_haiku_cost(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        cost = anthropic_cost("claude-3-5-haiku-20241022", usage)
        assert cost is not None
        rates = ANT_TABLE["claude-3-5-haiku"]
        expected = (1000 / 1000 * rates["input"]) + (500 / 1000 * rates["output"])
        assert cost == round(expected, 6)

    def test_claude35_haiku_cheaper_than_sonnet(self):
        """Haiku should be cheaper than Sonnet for same usage."""
        usage = {"input_tokens": 1000, "output_tokens": 500}
        haiku_cost = anthropic_cost("claude-3-5-haiku-20241022", usage)
        sonnet_cost = anthropic_cost("claude-3-5-sonnet-20241022", usage)
        assert haiku_cost is not None
        assert sonnet_cost is not None
        assert haiku_cost < sonnet_cost

    def test_unknown_model_returns_none(self):
        usage = {"input_tokens": 1000, "output_tokens": 500}
        assert anthropic_cost("gemini-pro", usage) is None

    def test_zero_tokens(self):
        usage = {"input_tokens": 0, "output_tokens": 0}
        cost = anthropic_cost("claude-3-opus-20240229", usage)
        assert cost == 0.0

    def test_missing_token_keys(self):
        cost = anthropic_cost("claude-3-opus-20240229", {})
        assert cost == 0.0

    def test_all_known_models_have_costs(self):
        """Every model key in the cost table should produce a valid cost."""
        usage = {"input_tokens": 1000, "output_tokens": 500}
        for model_key in ANT_TABLE:
            cost = anthropic_cost(model_key, usage)
            assert cost is not None, f"No cost for {model_key}"
            assert cost >= 0, f"Negative cost for {model_key}"
