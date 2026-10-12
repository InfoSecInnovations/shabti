"""What the LLM is asked, and how.

chatlas is stubbed on the module under test, so nothing reaches llama.cpp: these tests are about
the conversation handed to it - instructions as the system prompt, the question and what grounds it
as the user's turn - and about the stream coming back untouched, or failing as one of our errors.
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
from ...src.app.functionality.prompting import stream_response


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


async def run(**overrides):
    return [
        x
        async for x in stream_response(
            **{
                "model_name": "chat-1",
                "context": "the context",
                "task_prompt": "the task",
                "user_input": "the question",
                **overrides,
            }
        )
    ]


async def test_the_loaded_model_is_asked_on_the_llm_host(chat):
    await run()
    assert chat[0].kwargs["model"] == "chat-1"
    assert chat[0].kwargs["base_url"] == "http://llm-host:11434/v1"


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
    assert kwargs == {"content": "all"}


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
    assert raised.value.model == "chat-1"
    assert raised.value.message
    assert UPSTREAM_TEXT not in raised.value.message


async def test_the_answer_so_far_is_passed_on_before_the_failure(failing_chat):
    failing_chat(openai.APIConnectionError(request=REQUEST))
    received = []
    with pytest.raises(ProviderUnavailableError):
        async for x in stream_response(
            model_name="chat-1",
            context="the context",
            task_prompt="the task",
            user_input="the question",
        ):
            received.append(x)
    assert received == ["Hello"]
