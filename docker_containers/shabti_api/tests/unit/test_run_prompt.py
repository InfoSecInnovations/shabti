"""What a prompt makes of the LLM's stream, chunk by chunk.

No OpenSearch and no llama.cpp: the retrieval result, the loaded model, the LLM stream and the
audit call are all stubbed on the module under test, so every test here is a statement about what
happens between them. The prompter config is read for real, as in `test_prompt_info_validator`.

The stubbed stream yields what `prompting.stream_response` yields - chatlas's `content="all"`
stream, which is text as plain strings and reasoning as `ContentThinkingDelta`.
"""

import pytest
from chatlas.types import ContentThinkingDelta
from shabti_types import CollectionInfo, PromptInfo

from ...src.app.functionality import run_prompt as run_prompt_module
from ...src.app.functionality.load_prompter_config import load_prompter_config
from ...src.app.functionality.run_prompt import run_prompt

TASK = "question"
# read rather than named, so renaming one in the config does not fail a test about streaming
PERSONA = sorted(load_prompter_config("personas"))[0]
ENHANCER = sorted(load_prompter_config("enhancers"))[0]
# a task that only finds the sources, never generating a response: see docs/developer/PROMPTER.md
SEARCH = sorted(
    task
    for task, config in load_prompter_config("tasks").items()
    if not config.get("prompt")
)[0]
NO_SOURCES = (
    "No sources were found matching your query. Please refine your request to closer match the "
    "data in the database or ingest more data."
)


def thought(text):
    return ContentThinkingDelta(thinking=text)


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
        "text": f"{document_id} page {page_number} says something",
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


def thinking(chunks):
    return [chunk.thinking for chunk in chunks if chunk.thinking is not None]


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
    """`llm(item, item, ...)` is the stream the prompt will be reading. Returns the arguments the
    prompt asked the LLM with, filled in once the prompt has run."""

    def stream(*items):
        asked = {}

        async def stream_response(**kwargs):
            asked.update(kwargs)
            for item in items:
                yield item

        monkeypatch.setattr(run_prompt_module, "stream_response", stream_response)
        return asked

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


async def test_an_empty_string_is_not_a_response(context, chat_model, audit, llm):
    llm("", "Hello")
    assert responses(await run()) == ["Hello"]


async def test_thinking_is_forwarded_apart_from_the_response(
    logging_on, context, chat_model, audit, llm
):
    # the UI shows reasoning in its own panel, and the audit log holds only what was answered
    llm(thought("Let me"), thought(" see"), thought(""), "Hello")
    chunks = await run()
    assert thinking(chunks) == ["Let me", " see"]
    assert responses(chunks) == ["Hello"]
    assert audit[0]["prompt"]["response"] == "Hello"


async def test_persona_and_enhancers_reach_the_llm(context, chat_model, audit, llm):
    # the enhancers were once looked up, logged, and never handed on
    asked = llm("Hello")
    await run(persona=PERSONA, enhancers=[ENHANCER])
    assert (
        asked["persona_prompt"] == load_prompter_config("personas")[PERSONA]["prompt"]
    )
    assert asked["enhancer_prompts"] == [
        load_prompter_config("enhancers")[ENHANCER]["prompt"]
    ]


async def test_every_source_comes_before_the_response(context, chat_model, audit, llm):
    # the UI renders the references above the answer, and gets them in the order they arrive
    context["sources"] = [source("doc-1", 1), source("doc-2", 7)]
    llm("Hello")
    chunks = await run()
    assert [chunk.source is not None for chunk in chunks] == [True, True, False]
    found = sources(chunks)
    assert [item.document_metadata.document_id for item in found] == ["doc-1", "doc-2"]
    assert [item.page_metadata.page_number for item in found] == [1, 7]


async def test_a_source_carries_the_chunk_it_was_cited_for(
    context, chat_model, audit, llm
):
    # the page only says where to look; the chunk is what the LLM was actually given
    context["sources"] = [source("doc-1", 1), source("doc-2", 7)]
    llm("Hello")
    found = sources(await run())
    assert [item.text for item in found] == [
        "doc-1 page 1 says something",
        "doc-2 page 7 says something",
    ]


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
    # the entry is written after the stream ends, so it holds every piece of the response
    llm("Hello", " world")
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
    llm("Hello")
    await run(persona=PERSONA, enhancers=[ENHANCER])
    assert audit[0]["prompt"]["persona"] == PERSONA
    assert audit[0]["prompt"]["enhancers"] == [ENHANCER]


async def test_nothing_is_logged_when_logging_is_off(
    logging_off, context, chat_model, audit, llm
):
    llm("Hello")
    assert responses(await run()) == ["Hello"]
    assert audit == []


@pytest.fixture
def no_llm(monkeypatch):
    """Any attempt to reach the chat model fails the test."""

    async def get_loaded_chat_model():
        raise AssertionError("a search looked for a chat model")

    def stream_response(**kwargs):
        raise AssertionError("a search asked the LLM to answer")

    monkeypatch.setattr(
        run_prompt_module, "get_loaded_chat_model", get_loaded_chat_model
    )
    monkeypatch.setattr(run_prompt_module, "stream_response", stream_response)


async def test_a_search_returns_only_the_sources(context, no_llm, audit):
    context["sources"] = [source("doc-1", 1), source("doc-2", 7)]
    chunks = await run(task=SEARCH)
    assert [chunk.source is not None for chunk in chunks] == [True, True]
    assert [item.document_metadata.document_id for item in sources(chunks)] == [
        "doc-1",
        "doc-2",
    ]


async def test_a_search_is_audited_without_a_response(
    logging_on, context, no_llm, audit
):
    # nothing was generated, so there is no response to record, not an empty one
    await run(task=SEARCH)
    assert audit[0]["prompt"] == {
        "sources": context["sources"],
        "input": "what is in this collection?",
        "task": SEARCH,
    }


async def test_a_search_ignores_what_only_shapes_a_response(
    monkeypatch, logging_on, context, no_llm, audit
):
    # a persona or enhancers sent along with a search have nothing to shape, so they are neither
    # loaded nor recorded as if they had been used
    reads = []

    def load(directory):
        reads.append(directory)
        return load_prompter_config(directory)

    monkeypatch.setattr(run_prompt_module, "load_prompter_config", load)
    await run(task=SEARCH, persona=PERSONA, enhancers=[ENHANCER])
    assert reads == ["tasks"]
    assert "persona" not in audit[0]["prompt"]
    assert "enhancers" not in audit[0]["prompt"]
