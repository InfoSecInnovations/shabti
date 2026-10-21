"""How a model can be told to think, and what it is sent to tell it.

Each source says it differently, so these tests pin what each one's shape becomes - always in the
provider's own words - and which fields each kind of provider is sent for a value.
"""

import pytest

from ...src.app.functionality.providers.config import Provider
from ...src.app.functionality.providers.reasoning import (
    from_listing,
    from_models_dev,
    from_override,
    from_template,
    reasoning_format,
    reasoning_request,
)
from ...src.app.functionality.providers.registry import local_provider


def provider(base_url="https://api.test/v1", **kwargs):
    return Provider(id="remote", name="Remote", base_url=base_url, **kwargs)


@pytest.mark.parametrize(
    "options, expected",
    [
        (
            [{"type": "effort", "values": ["low", "medium", "high"]}],
            ["low", "medium", "high"],
        ),
        # kept as they're spelled, whatever they are
        (
            [{"type": "effort", "values": ["none", "minimal", "xhigh", "max"]}],
            ["none", "minimal", "xhigh", "max"],
        ),
        ([{"type": "toggle"}], [False, True]),
        (
            [{"type": "toggle"}, {"type": "effort", "values": ["low", "high"]}],
            [False, "low", "high"],
        ),
        # a level already turns it off
        (
            [{"type": "toggle"}, {"type": "effort", "values": ["none", "high"]}],
            ["none", "high"],
        ),
        ([{"type": "toggle"}, {"type": "budget_tokens"}], [False, True]),
    ],
)
def test_models_dev_options_are_offered(options, expected):
    assert from_models_dev({"reasoning_options": options}) == (expected, None)


@pytest.mark.parametrize(
    "known",
    [
        {},
        # a model that always thinks, as it can't be told otherwise
        {"reasoning_options": []},
        # a number of tokens is no level to pick from
        {"reasoning_options": [{"type": "budget_tokens", "min": 1024}]},
        # nothing to choose between
        {"reasoning_options": [{"type": "effort", "values": ["high"]}]},
    ],
)
def test_models_dev_offers_nothing_without_a_choice(known):
    assert from_models_dev(known) == (None, None)


def test_openrouter_efforts_start_from_the_lowest():
    listed = {
        "reasoning": {
            "mandatory": False,
            "supported_efforts": ["max", "high", "low", "none"],
            "default_effort": "high",
        }
    }
    assert from_listing(listed) == (["none", "low", "high", "max"], "high")


def test_openrouter_efforts_that_can_stop_without_none_are_switched_off():
    listed = {"reasoning": {"mandatory": False, "supported_efforts": ["high", "low"]}}
    assert from_listing(listed)[0] == [False, "low", "high"]


def test_openrouter_mandatory_efforts_cant_be_switched_off():
    listed = {"reasoning": {"mandatory": True, "supported_efforts": ["high", "low"]}}
    assert from_listing(listed)[0] == ["low", "high"]


def test_openrouter_without_efforts_is_a_switch():
    listed = {"reasoning": {"mandatory": False, "default_enabled": True}}
    assert from_listing(listed) == ([False, True], True)


@pytest.mark.parametrize("reasoning", [None, {"mandatory": True}])
def test_openrouter_offers_nothing_for_a_model_that_cant_be_told(reasoning):
    assert from_listing({"reasoning": reasoning}) == (None, None)


def test_anthropic_capabilities_offer_its_efforts_and_disabling():
    listed = {
        "capabilities": {
            "thinking": {
                "types": {
                    "adaptive": {"supported": True},
                    "enabled": {"supported": False},
                    "disabled": {"supported": True},
                }
            },
            "effort": {
                "low": {"supported": True},
                "medium": {"supported": True},
                "max": {"supported": False},
            },
        }
    }
    assert from_listing(listed) == ([False, "low", "medium"], None)


def test_anthropic_without_efforts_is_a_switch():
    listed = {
        "capabilities": {
            "thinking": {
                "types": {
                    "adaptive": {"supported": True},
                    "disabled": {"supported": True},
                }
            }
        }
    }
    assert from_listing(listed)[0] == [False, True]


def test_a_listing_that_says_nothing_offers_nothing():
    assert from_listing({"id": "chat"}) == (None, None)


def test_a_harmony_template_takes_gpt_oss_efforts():
    template = (
        "{{- 'Reasoning: ' + reasoning_effort }}<|start|>assistant<|channel|>final"
    )
    assert from_template(template) == (["low", "medium", "high"], "medium")


def test_a_template_offers_the_efforts_it_compares_against():
    template = (
        "{%- if reasoning_effort == 'low' %}be brief"
        '{%- elif reasoning_effort == "high" %}be thorough{%- endif %}'
    )
    assert from_template(template)[0] == ["low", "high"]


@pytest.mark.parametrize(
    "template",
    [
        # Qwen3
        "{%- if enable_thinking is defined and enable_thinking is false %}<think>\n\n</think>",
        # DeepSeek V3.1
        "{%- if not thinking is defined %}{%- set thinking = false %}{%- endif %}",
        # Granite
        "{%- if thinking %}Think first.{%- endif %}",
    ],
)
def test_a_template_with_a_switch_is_switched(template):
    assert from_template(template) == ([False, True], None)


def test_a_template_that_always_thinks_offers_nothing():
    assert from_template("<think>{{ message.reasoning_content }}</think>") == (
        None,
        None,
    )


def test_an_override_is_offered_with_its_default():
    override = {"reasoning": ["low", "high"], "reasoning_default": "high"}
    assert from_override(override) == (["low", "high"], "high")


def test_an_empty_override_offers_nothing_rather_than_nothing_set():
    # so the admin can take away a control discovery got wrong
    assert from_override({"reasoning": []}) == (None, None)
    assert from_override({"label": "Chat"}) is None


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.anthropic.com/v1", "anthropic"),
        ("https://api.deepseek.com", "thinking"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "enable_thinking"),
        ("https://api.openai.com/v1", "effort"),
        ("https://self-hosted.test/v1", "effort"),
    ],
)
def test_the_format_is_told_from_the_host(base_url, expected):
    assert reasoning_format(provider(base_url)) == expected


def test_a_configured_format_beats_the_host():
    configured = provider("https://openrouter.ai/api/v1", reasoning_format="llama_cpp")
    assert reasoning_format(configured) == "llama_cpp"


def test_the_local_models_are_llama_cpp(monkeypatch):
    monkeypatch.setenv("LLM_HOST", "llm-host")
    assert reasoning_format(local_provider()) == "llama_cpp"


@pytest.mark.parametrize(
    "reasoning_format, value, expected",
    [
        ("effort", "high", {"reasoning_effort": "high"}),
        ("effort", "none", {"reasoning_effort": "none"}),
        ("effort", False, {"reasoning_effort": "none"}),
        ("effort", True, {}),
        ("llama_cpp", "low", {"reasoning_effort": "low"}),
        (
            "llama_cpp",
            False,
            {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
        ),
        (
            "llama_cpp",
            True,
            {"chat_template_kwargs": {"enable_thinking": True, "thinking": True}},
        ),
        ("openrouter", "xhigh", {"reasoning": {"effort": "xhigh"}}),
        ("openrouter", False, {"reasoning": {"enabled": False}}),
        ("thinking", False, {"thinking": {"type": "disabled"}}),
        ("thinking", "none", {"thinking": {"type": "disabled"}}),
        ("thinking", True, {"thinking": {"type": "enabled"}}),
        (
            "thinking",
            "max",
            {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
        ),
        ("enable_thinking", False, {"enable_thinking": False}),
        ("enable_thinking", "high", {"enable_thinking": True}),
        ("anthropic", False, {"thinking": {"type": "disabled"}}),
        ("anthropic", True, {"thinking": {"type": "adaptive"}}),
        (
            "anthropic",
            "low",
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        ),
    ],
)
def test_each_format_is_sent_its_own_fields(reasoning_format, value, expected):
    assert (
        reasoning_request(provider(reasoning_format=reasoning_format), value)
        == expected
    )
