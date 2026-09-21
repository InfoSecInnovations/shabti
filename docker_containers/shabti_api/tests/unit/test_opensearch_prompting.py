"""What a question looks like by the time the embeddings model sees it.

An asymmetric model was trained with an instruction in front of a search query and nothing in front
of the passages searched. Embedding the question raw still retrieves, just measurably worse, so
there is nothing to notice: this is the only thing asserting the instruction is applied at all.
"""

import pytest

from ...src.app.functionality import opensearch_prompting
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch


class Client:
    """An index with nothing in it, so retrieval stops after the search."""

    async def search(self, body, index):
        return {"hits": {"hits": []}}


@pytest.fixture
def embedded(monkeypatch):
    """Captures what was sent to the embeddings server, for a model wanting `prefix`."""
    sent = []

    def create_embeddings(text, model_id=None):
        sent.append((text, model_id))
        return [0.1]

    async def get_ingesting_document_ids(collection_id):
        return []

    def configure(prefix):
        monkeypatch.setattr(opensearch_prompting, "get_client", Client)
        monkeypatch.setattr(
            opensearch_prompting, "get_embeddings_model_id", lambda: "embed-1"
        )
        monkeypatch.setattr(opensearch_prompting, "query_prefix", lambda _: prefix)
        monkeypatch.setattr(
            opensearch_prompting, "create_embeddings", create_embeddings
        )
        monkeypatch.setattr(
            opensearch_prompting,
            "get_ingesting_document_ids",
            get_ingesting_document_ids,
        )
        return sent

    return configure


async def test_a_query_is_asked_the_way_the_model_expects(embedded):
    sent = embedded("Represent this sentence for searching relevant passages: ")
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert sent == [
        (
            "Represent this sentence for searching relevant passages: what is a shabti?",
            "embed-1",
        )
    ]


async def test_a_model_wanting_no_instruction_gets_the_question_as_asked(embedded):
    # the shipped model is symmetric, and prepending anything to its input would be noise
    sent = embedded("")
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert sent == [("what is a shabti?", "embed-1")]


async def test_the_model_is_named_rather_than_looked_up_again(embedded):
    # the prefix can only be found once the model is known, and passing it on is what keeps that
    # from costing a second listing inside create_embeddings
    sent = embedded("query: ")
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert sent[0][1] == "embed-1"


async def test_a_blank_question_is_not_turned_into_a_search_for_the_instruction(
    embedded,
):
    # create_embeddings returns nothing for empty text, and prefixing it would make "nothing to
    # search for" into a search for the instruction, which matches arbitrary passages
    sent = embedded("Represent this sentence for searching relevant passages: ")
    await get_context_from_opensearch("collection-1", 5, "   ")
    assert sent == [("   ", "embed-1")]
