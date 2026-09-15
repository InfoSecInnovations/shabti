"""What a prompt makes of the LLM's stream, chunk by chunk.

No OpenSearch and no llama.cpp: the retrieval result, the loaded model, the SSE stream and the
audit call are all stubbed on the module under test, so every test here is a statement about the
parsing between them. The prompter config is read for real, as in `test_prompt_info_validator`.

The stubbed stream yields exactly what `prompting.stream_response` yields - a raw SSE data line
with a trailing newline - because that trailing newline is why the `[DONE]` sentinel is matched
with `startswith` rather than `==`.
"""

import json

import pytest
from shabti_types import CollectionInfo, PromptInfo

from ...src.app.functionality import run_prompt as run_prompt_module
from ...src.app.functionality.load_prompter_config import load_prompter_config
from ...src.app.functionality.run_prompt import run_prompt

TASK = "question"
# read rather than named, so renaming one in the config does not fail a test about streaming
PERSONA = sorted(load_prompter_config("personas"))[0]
ENHANCER = sorted(load_prompter_config("enhancers"))[0]
NO_SOURCES = (
    "No sources were found matching your query. Please refine your request to closer match the "
    "data in the database or ingest more data."
)


def chunk(**delta):
    """One `chat.completion.chunk`, as llama.cpp writes it."""
    return json.dumps(
        {"choices": [{"finish_reason": None, "index": 0, "delta": delta}]}
    )


# llama.cpp opens every stream with this: the key is there, the value is not. Taken from a real
# b10630 response, and the reason a prompt used to die on its first chunk
ROLE = chunk(role="assistant", content=None)
FINISH = json.dumps({"choices": [{"finish_reason": "stop", "index": 0, "delta": {}}]})
DONE = "[DONE]"


def source(document_id, page_number):
    return {
        "doc_metadata": {
            "source": "a-doc.txt",
            "ingest_date": 1751630796259,
            "filename": "a-doc.txt",
            "media_type": "text/plain",
            "document_id": document_id,
            "page_count": 2,
            "vector_count": 4,
            "languages": ["en"],
        },
        "page_metadata": {"page_number": page_number, "source": "a-doc.txt"},
    }


def prompt_info(**overrides):
    return PromptInfo(
        **{
            "collection_id": "a-collection",
            "task": TASK,
            "user_input": "what is in this collection?",
            **overrides,
        }
    )


async def run(**overrides):
    return [chunk async for chunk in run_prompt(None, prompt_info(**overrides))]


def responses(chunks):
    return [chunk.response for chunk in chunks if chunk.response is not None]


def sources(chunks):
    return [chunk.source for chunk in chunks if chunk.source is not None]


@pytest.fixture
def logging_on(monkeypatch):
    monkeypatch.setenv("SHABTI_LOGGING_ENABLED", "True")


@pytest.fixture
def logging_off(monkeypatch):
    monkeypatch.setenv("SHABTI_LOGGING_ENABLED", "False")


@pytest.fixture
def context(monkeypatch):
    """What retrieval found. Mutable, so a test can empty it to reach the no-sources path."""
    found = {"context": "a-doc.txt says something", "sources": [source("doc-1", 1)]}

    async def get_context_from_opensearch(collection_id, limit, user_input):
        return found

    monkeypatch.setattr(
        run_prompt_module, "get_context_from_opensearch", get_context_from_opensearch
    )
    return found


@pytest.fixture
def chat_model(monkeypatch):
    async def get_loaded_chat_model():
        return "chat-1"

    monkeypatch.setattr(
        run_prompt_module, "get_loaded_chat_model", get_loaded_chat_model
    )


@pytest.fixture
def llm(monkeypatch):
    """`llm(line, line, ...)` is the SSE stream the prompt will be parsing."""

    def stream(*lines):
        async def stream_response(**kwargs):
            for line in lines:
                yield f"{line}\n"

        monkeypatch.setattr(run_prompt_module, "stream_response", stream_response)

    return stream


@pytest.fixture
def audit(monkeypatch):
    """The audit entries the prompt asked for, with no handler or file in the way."""
    entries = []

    async def log_user_action(token, action, message, **kwargs):
        entries.append({"action": action, "message": message, **kwargs})

    async def get_collection_info(collection_id):
        return CollectionInfo(
            collection_id=collection_id, collection_name="A collection"
        )

    monkeypatch.setattr(run_prompt_module, "log_user_action", log_user_action)
    monkeypatch.setattr(run_prompt_module, "get_collection_info", get_collection_info)
    return entries


@pytest.mark.parametrize("enabled", ["True", "False"])
async def test_the_opening_null_content_delta_is_not_a_response(
    monkeypatch, context, chat_model, audit, llm, enabled
):
    # the regression: `"content" in delta` is a membership test, so the null passed it and
    # `response += None` took out the whole stream - but only with the audit log accumulating,
    # which is why both settings are checked
    monkeypatch.setenv("SHABTI_LOGGING_ENABLED", enabled)
    llm(ROLE, chunk(content="Hello"), chunk(content=" world"), FINISH, DONE)
    assert responses(await run()) == ["Hello", " world"]


async def test_the_finish_delta_is_not_a_response(context, chat_model, audit, llm):
    # the last chunk carries a `finish_reason` and an empty delta, and nothing to show a user
    llm(chunk(content="Hello"), FINISH, DONE)
    assert responses(await run()) == ["Hello"]


async def test_an_empty_content_delta_is_not_a_response(
    context, chat_model, audit, llm
):
    llm(chunk(content=""), chunk(content="Hello"), DONE)
    assert responses(await run()) == ["Hello"]


async def test_done_ends_the_stream(context, chat_model, audit, llm):
    llm(chunk(content="Hello"), DONE, chunk(content=" world"))
    assert responses(await run()) == ["Hello"]


async def test_a_line_that_is_neither_a_chunk_nor_done_is_raised(
    context, chat_model, audit, llm
):
    # an LLM answering a stream with an error body is worth failing on, not silently dropping
    llm(chunk(content="Hello"), "<html>502 Bad Gateway</html>")
    with pytest.raises(json.decoder.JSONDecodeError):
        await run()


async def test_every_source_comes_before_the_response(context, chat_model, audit, llm):
    # the UI renders the references above the answer, and gets them in the order they arrive
    context["sources"] = [source("doc-1", 1), source("doc-2", 7)]
    llm(ROLE, chunk(content="Hello"), DONE)
    chunks = await run()
    assert [chunk.source is not None for chunk in chunks] == [True, True, False]
    found = sources(chunks)
    assert [item.document_metadata.document_id for item in found] == ["doc-1", "doc-2"]
    assert [item.page_metadata.page_number for item in found] == [1, 7]


async def test_no_sources_says_so_without_reaching_the_llm(
    monkeypatch, context, chat_model, audit
):
    context["context"] = ""
    context["sources"] = []

    def stream_response(**kwargs):
        raise AssertionError("the LLM was asked to answer with nothing to ground it in")

    monkeypatch.setattr(run_prompt_module, "stream_response", stream_response)
    assert responses(await run()) == [NO_SOURCES]
    assert audit == []


async def test_the_audit_entry_holds_the_response_the_user_saw(
    logging_on, context, chat_model, audit, llm
):
    # the entry is written after the stream ends, so a `[DONE]` that returned rather than broke
    # left every completed prompt unlogged
    llm(ROLE, chunk(content="Hello"), chunk(content=" world"), FINISH, DONE)
    await run()
    assert len(audit) == 1
    entry = audit[0]
    assert entry["action"] == "QUERY"
    assert entry["message"] == "User ran a prompt on collection with ID a-collection"
    assert entry["collection"]["collection_id"] == "a-collection"
    assert entry["prompt"] == {
        "response": "Hello world",
        "sources": context["sources"],
        "input": "what is in this collection?",
        "task": TASK,
    }


async def test_the_audit_entry_records_what_shaped_the_prompt(
    logging_on, context, chat_model, audit, llm
):
    llm(chunk(content="Hello"), DONE)
    await run(persona=PERSONA, enhancers=[ENHANCER])
    assert audit[0]["prompt"]["persona"] == PERSONA
    assert audit[0]["prompt"]["enhancers"] == [ENHANCER]


async def test_nothing_is_logged_when_logging_is_off(
    logging_off, context, chat_model, audit, llm
):
    llm(ROLE, chunk(content="Hello"), DONE)
    assert responses(await run()) == ["Hello"]
    assert audit == []
