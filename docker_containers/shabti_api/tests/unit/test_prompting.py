"""What the LLM is asked, and how.

chatlas is stubbed on the module under test, so nothing reaches a provider: these tests are about
the conversation handed to it - instructions as the system prompt, the question and what grounds it
as the user's turn - about where it is sent and with what credentials, and about the stream coming
back untouched, or failing as one of our errors.
"""

import httpx
import openai
import pytest
from chatlas.types import ContentThinkingDelta
from shabti_types import (
    ProviderAuthError,
    ProviderNotFoundError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

from ...src.app.functionality import prompting
from ...src.app.functionality.prompting import REMOTE_TIMEOUT, stream_response
from ...src.app.functionality.providers.config import Provider
from ...src.app.functionality.providers.registry import ChatModel, local_provider

REMOTE = Provider(
    id="remote",
    name="Remote",
    base_url="https://api.test/v1",
    headers={"X-Org": "an-org"},
    user_id_field="safety_identifier",
)


class Chat:
    """Stands in for `ChatOpenAICompletions`, recording what it was built and asked with."""

    built = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.asked = None
        Chat.built.append(self)

    async def stream_async(self, *args, **kwargs):
        self.asked = (args, kwargs)

        async def stream():
            for item in ["Hello", ContentThinkingDelta(thinking="hmm"), " world"]:
                yield item

        return stream()


@pytest.fixture
def chat(monkeypatch):
    Chat.built = []
    monkeypatch.setenv("LLM_HOST", "llm-host")
    monkeypatch.setattr(prompting, "ChatOpenAICompletions", Chat)
    return Chat.built


def local_model():
    return ChatModel(provider=local_provider(), model_id="chat-1")


async def run(**overrides):
    return [
        x
        async for x in stream_response(
            **{
                "model": local_model(),
                "api_key": None,
                "user": None,
                "context": "the context",
                "task_prompt": "the task",
                "user_input": "the question",
                **overrides,
            }
        )
    ]


async def test_a_local_model_is_asked_on_the_llm_host(chat):
    await run()
    assert chat[0].kwargs["model"] == "chat-1"
    assert chat[0].kwargs["base_url"] == "http://llm-host:11434/v1"
    # llama.cpp doesn't check it, but the SDK won't start without one
    assert chat[0].kwargs["api_key"] == "none"
    # a long answer on a slow machine is still an answer
    assert chat[0].kwargs["kwargs"]["timeout"] is None


async def test_a_remote_model_is_asked_on_its_provider_with_the_key(chat):
    await run(model=ChatModel(provider=REMOTE, model_id="vendor/chat"), api_key="sk-1")
    assert chat[0].kwargs["model"] == "vendor/chat"
    assert chat[0].kwargs["base_url"] == "https://api.test/v1"
    assert chat[0].kwargs["api_key"] == "sk-1"
    assert chat[0].kwargs["kwargs"]["default_headers"] == {"X-Org": "an-org"}
    assert chat[0].kwargs["kwargs"]["timeout"] == REMOTE_TIMEOUT


async def test_a_keyless_remote_provider_is_asked_without_a_key(chat):
    await run(model=ChatModel(provider=REMOTE, model_id="vendor/chat"))
    assert chat[0].kwargs["api_key"] == "none"


async def test_the_user_goes_in_the_field_the_provider_names(chat):
    await run(model=ChatModel(provider=REMOTE, model_id="vendor/chat"), user="a-hash")
    _, kwargs = chat[0].asked
    assert kwargs["kwargs"] == {"extra_body": {"safety_identifier": "a-hash"}}


async def test_no_user_is_sent_without_one(chat):
    await run()
    _, kwargs = chat[0].asked
    assert kwargs["kwargs"] is None


async def test_reasoning_goes_in_the_body_beside_the_user(chat):
    await run(
        model=ChatModel(provider=REMOTE, model_id="vendor/chat"),
        user="a-hash",
        reasoning="high",
    )
    _, kwargs = chat[0].asked
    assert kwargs["kwargs"] == {
        "extra_body": {"safety_identifier": "a-hash", "reasoning_effort": "high"}
    }


async def test_a_local_model_is_switched_through_its_template(chat):
    await run(reasoning=False)
    _, kwargs = chat[0].asked
    assert kwargs["kwargs"] == {
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": False, "thinking": False}
        }
    }


async def test_a_model_left_to_think_by_default_is_sent_nothing(chat):
    # true, on a provider that only takes levels, is the default it would think at anyway
    await run(model=ChatModel(provider=REMOTE, model_id="vendor/chat"), reasoning=True)
    _, kwargs = chat[0].asked
    assert kwargs["kwargs"] is None


async def test_the_instructions_are_the_system_prompt(chat):
    # persona first, as who is answering frames what is asked of them
    await run(persona_prompt="the persona", enhancer_prompts=["one", "two"])
    assert chat[0].kwargs["system_prompt"] == "the persona\n\nthe task\n\none\n\ntwo"


async def test_the_system_prompt_is_the_task_alone_without_extras(chat):
    await run()
    assert chat[0].kwargs["system_prompt"] == "the task"


async def test_the_question_and_its_grounding_are_the_user_turn(chat):
    await run(source_file_contents="the file")
    args, kwargs = chat[0].asked
    assert args == (
        "Context: the context\n\nUser input: the question\n\nSource file: the file",
    )
    assert kwargs["content"] == "all"


async def test_the_user_turn_has_no_source_file_without_one(chat):
    await run()
    args, _ = chat[0].asked
    assert args == ("Context: the context\n\nUser input: the question",)


async def test_the_stream_is_passed_on_as_it_comes(chat):
    # reasoning included: telling it apart from the answer is the caller's job
    assert await run() == ["Hello", ContentThinkingDelta(thinking="hmm"), " world"]


REQUEST = httpx.Request("POST", "http://llm-host:11434/v1/chat/completions")
# what an upstream error body might hold, which must never reach the user
UPSTREAM_TEXT = "invalid key sk-secret"


def status_error(error_class, status):
    return error_class(
        UPSTREAM_TEXT, response=httpx.Response(status, request=REQUEST), body=None
    )


@pytest.fixture
def failing_chat(monkeypatch):
    """`failing_chat(error)` makes the stream fail with it, after some of the answer is out."""

    def fail_with(error):
        class FailingChat(Chat):
            async def stream_async(self, *args, **kwargs):
                async def stream():
                    yield "Hello"
                    raise error

                return stream()

        monkeypatch.setenv("LLM_HOST", "llm-host")
        monkeypatch.setattr(prompting, "ChatOpenAICompletions", FailingChat)

    return fail_with


@pytest.mark.parametrize(
    "error, expected",
    [
        (status_error(openai.AuthenticationError, 401), ProviderAuthError),
        (status_error(openai.PermissionDeniedError, 403), ProviderAuthError),
        (status_error(openai.APIStatusError, 402), ProviderQuotaError),
        (status_error(openai.NotFoundError, 404), ProviderNotFoundError),
        (status_error(openai.RateLimitError, 429), ProviderRateLimitError),
        (status_error(openai.InternalServerError, 500), ProviderUnavailableError),
        (openai.APIConnectionError(request=REQUEST), ProviderUnavailableError),
        (openai.APITimeoutError(request=REQUEST), ProviderUnavailableError),
    ],
)
async def test_an_sdk_failure_is_reported_as_a_provider_error(
    failing_chat, error, expected
):
    failing_chat(error)
    with pytest.raises(expected) as raised:
        await run()
    # qualified, so the message says which provider failed
    assert raised.value.model == "local/chat-1"
    assert raised.value.message
    assert UPSTREAM_TEXT not in raised.value.message


async def test_the_answer_so_far_is_passed_on_before_the_failure(failing_chat):
    failing_chat(openai.APIConnectionError(request=REQUEST))
    received = []
    with pytest.raises(ProviderUnavailableError):
        async for x in stream_response(
            model=local_model(),
            api_key=None,
            user=None,
            context="the context",
            task_prompt="the task",
            user_input="the question",
        ):
            received.append(x)
    assert received == ["Hello"]


async def test_the_providers_reason_is_passed_on_without_the_key(failing_chat):
    # a rate limit is only actionable with the provider's reason: per minute is waited out, per
    # day isn't
    failing_chat(
        openai.RateLimitError(
            UPSTREAM_TEXT,
            response=httpx.Response(429, request=REQUEST),
            body={"message": "Rate limit exceeded for sk-secret: free-models-per-day"},
        )
    )
    with pytest.raises(ProviderRateLimitError) as raised:
        await run(api_key="sk-secret")
    assert "(chat model local/chat-1)" in raised.value.message
    assert raised.value.message.endswith(
        ": Rate limit exceeded for ***: free-models-per-day"
    )
