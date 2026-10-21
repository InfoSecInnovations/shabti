"""How a model can be told to think, and the request fields that tell it.

Neither is standard. Some providers take a level, where "none" is one of the levels, some a switch,
some both, and some models can't be told at all. So a model's values are kept in its provider's own
words - "minimal", "xhigh", "max" - and only a provider that has no word for on and off gets false
and true. Which fields carry a value depends on the provider, see `reasoning_request`.

Nothing lists the values everywhere either: OpenRouter and Anthropic say in their model listings,
models.dev knows most others, and a local model's chat template shows what it reads.
"""

import re
from .config import LOCAL, Provider
from .models_dev import host

Reasoning = bool | str
# a model's values, and the one it uses when a prompt doesn't say if that is known
Capability = tuple[list[Reasoning] | None, Reasoning | None]

NO_CAPABILITY: Capability = (None, None)

# the providers that don't take `reasoning_effort`, by the host they're served from. any other can be
# named in the providers file
HOST_FORMATS = {
    "openrouter.ai": "openrouter",
    "api.anthropic.com": "anthropic",
    "api.deepseek.com": "thinking",
    "api.moonshot.ai": "thinking",
    "api.moonshot.cn": "thinking",
    "api.z.ai": "thinking",
    "open.bigmodel.cn": "thinking",
    "dashscope.aliyuncs.com": "enable_thinking",
    "dashscope-intl.aliyuncs.com": "enable_thinking",
}

# gpt-oss's harmony format, whose template writes the effort into the system prompt as given
HARMONY_MARKER = "<|channel|>"
HARMONY_EFFORTS = ["low", "medium", "high"]
HARMONY_DEFAULT = "medium"
# the levels a template compares the effort it is given against
EFFORT_LITERAL = re.compile(r"reasoning_effort\s*==\s*['\"]([\w-]+)['\"]")
# a template that can be switched reads one of these, under either name
SWITCH = re.compile(
    r"\benable_thinking\b|\bthinking\s+is\s+(?:not\s+)?defined|\bif\s+(?:not\s+)?thinking\b"
)


def reasoning_format(provider: Provider) -> str:
    if provider.id == LOCAL:
        return "llama_cpp"
    return provider.reasoning_format or HOST_FORMATS.get(
        host(provider.base_url), "effort"
    )


def offered(values, *defaults) -> Capability:
    """The values, if they leave anything to choose, and the first of the defaults among them."""
    if not isinstance(values, list) or len(values) < 2:
        return NO_CAPABILITY
    return values, next((x for x in defaults if x is not None and x in values), None)


def from_models_dev(known: dict) -> Capability:
    options = known.get("reasoning_options")
    if not isinstance(options, list):
        return NO_CAPABILITY
    kinds = {x.get("type"): x for x in options if isinstance(x, dict)}
    values = list((kinds.get("effort") or {}).get("values") or [])
    # a switch alongside levels turns them off, unless one of the levels already does
    if "toggle" in kinds and "none" not in values:
        values = [False, *values] if values else [False, True]
    # a budget alone isn't offered: a number of tokens is no level to pick from
    return offered(values)


def from_openrouter(reasoning: dict) -> Capability:
    # highest first, where every other source starts from the lowest
    efforts = list(reversed(reasoning.get("supported_efforts") or []))
    can_stop = not reasoning.get("mandatory")
    if not efforts:
        values = [False, True] if can_stop else []
    elif can_stop and "none" not in efforts:
        values = [False, *efforts]
    else:
        values = efforts
    enabled = reasoning.get("default_enabled")
    return offered(
        values,
        reasoning.get("default_effort"),
        enabled,
        "none" if enabled is False else None,
    )


def from_anthropic(capabilities: dict) -> Capability:
    def supported(group: dict) -> list[str]:
        return [
            name
            for name, x in (group or {}).items()
            if isinstance(x, dict) and x.get("supported")
        ]

    thinking = supported((capabilities.get("thinking") or {}).get("types"))
    values = supported(capabilities.get("effort")) or (
        [True] if "adaptive" in thinking else []
    )
    if "disabled" in thinking:
        values = [False, *values]
    return offered(values)


def from_listing(listed: dict) -> Capability:
    """What a provider's own model listing says, where it says anything."""
    if isinstance(reasoning := listed.get("reasoning"), dict):
        return from_openrouter(reasoning)
    if isinstance(capabilities := listed.get("capabilities"), dict):
        return from_anthropic(capabilities)
    return NO_CAPABILITY


def from_template(template: str) -> Capability:
    """What a chat template reads. Only a guess, which a catalogue entry can correct."""
    if HARMONY_MARKER in template:
        return offered(HARMONY_EFFORTS, HARMONY_DEFAULT)
    if efforts := list(dict.fromkeys(EFFORT_LITERAL.findall(template))):
        return offered(efforts)
    if SWITCH.search(template):
        return offered([False, True])
    return NO_CAPABILITY


def from_override(override: dict) -> Capability | None:
    """What the admin set, which may be an empty list to offer nothing. None when they set nothing."""
    if "reasoning" not in override:
        return None
    return offered(override["reasoning"], override.get("reasoning_default"))


def first_known(*capabilities: Capability) -> Capability:
    return next((x for x in capabilities if x[0]), NO_CAPABILITY)


def reasoning_request(provider: Provider, value: Reasoning) -> dict:
    """The fields to add to a chat completion's body to ask for `value`."""
    switch = isinstance(value, bool)
    match reasoning_format(provider):
        case "llama_cpp":
            # templates read their switch under either name, and llama.cpp passes on whatever it is
            # given, so both are set rather than guessing which the model's template reads
            if switch:
                return {
                    "chat_template_kwargs": {
                        "enable_thinking": value,
                        "thinking": value,
                    }
                }
            return {"reasoning_effort": value}
        case "openrouter":
            return {"reasoning": {"enabled": value} if switch else {"effort": value}}
        case "thinking":
            if value is False or value == "none":
                return {"thinking": {"type": "disabled"}}
            enabled = {"thinking": {"type": "enabled"}}
            return enabled if switch else {**enabled, "reasoning_effort": value}
        case "enable_thinking":
            return {"enable_thinking": value if switch else value != "none"}
        case "anthropic":
            # its OpenAI compatible endpoint ignores `reasoning_effort`, but not the native fields
            if value is False:
                return {"thinking": {"type": "disabled"}}
            adaptive = {"thinking": {"type": "adaptive"}}
            return (
                adaptive if switch else {**adaptive, "output_config": {"effort": value}}
            )
        case _:
            # "none" is the level that turns it off, and a model that thinks is left to its default
            if switch:
                return {} if value else {"reasoning_effort": "none"}
            return {"reasoning_effort": value}
