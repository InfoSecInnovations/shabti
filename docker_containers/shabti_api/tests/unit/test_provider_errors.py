"""The errors a provider's failure is reported as, and what of its own answer they pass on.

A rate limit or a used up quota is only something a user can act on with the provider's reason,
so that is shown - with the key the request was made with taken out, as a provider can echo it.
"""

import json

import pytest
from shabti_types import (
    ProviderAuthError,
    ProviderNotFoundError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

from ...src.app.functionality.providers.errors import (
    DETAIL_LIMIT,
    provider_error,
    upstream_detail,
)

MODEL = "openrouter/nvidia/llama-nemotron-embed-vl-1b-v2:free"


@pytest.mark.parametrize(
    "status, error",
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (402, ProviderQuotaError),
        (404, ProviderNotFoundError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (None, ProviderUnavailableError),
    ],
)
def test_each_status_is_its_own_error(status, error):
    assert isinstance(provider_error(status, MODEL), error)


def test_the_error_says_what_the_model_was_for():
    # a chat whose question couldn't be embedded is not a chat model failing
    error = provider_error(429, MODEL, purpose="embeddings")
    assert error.message.endswith(f"(embeddings model {MODEL})")


def test_a_message_naming_the_model_already_is_not_told_twice():
    error = provider_error(404, MODEL, purpose="chat")
    assert error.message.count(MODEL) == 1


def test_the_providers_reason_follows():
    error = provider_error(
        429,
        MODEL,
        purpose="embeddings",
        detail="Rate limit exceeded: free-models-per-day",
    )
    assert error.message.endswith(": Rate limit exceeded: free-models-per-day")


def test_the_reason_is_read_from_an_openai_shaped_body():
    body = json.dumps(
        {"error": {"message": "Rate limit exceeded: free-models-per-min", "code": 429}}
    )
    assert upstream_detail(body) == "Rate limit exceeded: free-models-per-min"


def test_the_reason_is_read_from_a_parsed_error():
    # what the OpenAI SDK hands over as an error's body
    assert (
        upstream_detail({"message": "Quota exceeded", "code": 402}) == "Quota exceeded"
    )


@pytest.mark.parametrize(
    "body", [{"message": "Not ready"}, {"detail": "Not ready"}, {"error": "Not ready"}]
)
def test_other_shapes_of_body_are_read_too(body):
    assert upstream_detail(body) == "Not ready"


def test_a_plain_text_body_is_used_as_it_is():
    assert upstream_detail("upstream\n  overloaded ") == "upstream overloaded"


def test_the_key_is_taken_out():
    detail = upstream_detail(
        {"message": "key sk-or-123 is over its limit"}, "sk-or-123"
    )
    assert detail == "key *** is over its limit"


def test_a_bearer_token_is_taken_out():
    detail = upstream_detail("rejected Authorization: Bearer sk-elsewhere")
    assert detail == "rejected Authorization: Bearer ***"


def test_a_long_reason_is_cut_short():
    detail = upstream_detail("x" * 1000)
    assert len(detail) == DETAIL_LIMIT
    assert detail.endswith("…")


@pytest.mark.parametrize(
    "body", [None, "", "   ", {}, {"error": {"code": 429}}, [1, 2]]
)
def test_nothing_usable_is_no_reason(body):
    assert upstream_detail(body) is None
