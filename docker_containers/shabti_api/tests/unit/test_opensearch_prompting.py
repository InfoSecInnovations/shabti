"""What a question looks like by the time the embeddings model sees it.

An asymmetric model was trained with an instruction in front of a search query and nothing in front
of the passages searched. Embedding the question raw still retrieves, just measurably worse, so
there is nothing to notice: this is the only thing asserting the instruction is applied at all.

A question longer than the model can take in one go is split and averaged rather than refused, and
nothing downstream would notice that going wrong either: a vector is a vector.
"""

import math

import pytest

from ...src.app.functionality import opensearch_ingesting, opensearch_prompting
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch


class Client:
    """An index with nothing in it, so retrieval stops after the search."""

    def __init__(self):
        self.searches = []

    async def search(self, body, index):
        self.searches.append(body)
        return {"hits": {"hits": []}}


@pytest.fixture
def embedded(monkeypatch):
    """Captures what was sent to the embeddings server, for a model wanting `prefix`.

    A word stands in for a token, as in the splitter's tests, so that what counts as too long can be
    read off the question.
    """
    sent = []
    client = Client()

    def create_embeddings(text, model_id=None):
        sent.append((text, model_id))
        if isinstance(text, list):
            # a different direction per piece, so that averaging them is visible in the result
            return [[1.0, 0.0] if n % 2 == 0 else [0.0, 1.0] for n in range(len(text))]
        return [0.1]

    async def get_ingesting_document_ids(collection_id):
        return []

    def count_tokens(text, _):
        return len(text.split())

    def configure(prefix, chunk_size=128):
        monkeypatch.setattr(opensearch_prompting, "get_client", lambda: client)
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
        for module in (opensearch_prompting, opensearch_ingesting):
            monkeypatch.setattr(module, "count_tokens", count_tokens)
            monkeypatch.setattr(module, "chunk_size", lambda _: chunk_size)
        monkeypatch.setattr(opensearch_ingesting, "context_limit", lambda _: 512)
        return sent

    configure.client = client
    return configure


def searched_vector(embedded):
    return embedded.client.searches[0]["query"]["knn"]["document_vector"]["vector"]


# long enough to need several pieces at a chunk size of 100
LONG = " ".join(f"word{n}" for n in range(500))


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


async def test_a_question_at_the_chunk_size_is_embedded_whole(embedded):
    # the chunk size is measured with the prefix included, so this is exactly what fits
    sent = embedded("query: ", chunk_size=4)
    await get_context_from_opensearch("collection-1", 5, "one two three")
    assert sent == [("query: one two three", "embed-1")]


async def test_a_long_question_is_split_rather_than_refused(embedded):
    # llama.cpp refuses an input bigger than its physical batch outright, which used to break the
    # answer's stream with nothing said about why
    sent = embedded("query: ", chunk_size=100)
    await get_context_from_opensearch("collection-1", 5, LONG)
    # every piece in the one request rather than a round trip each
    assert len(sent) == 1
    pieces, model_id = sent[0]
    assert model_id == "embed-1"
    assert len(pieces) > 1
    for piece in pieces:
        assert piece.startswith("query: ")
        assert len(piece.split()) <= 100
    # nothing of the question is left out
    assert pieces[0].removeprefix("query: ").startswith("word0 ")
    assert pieces[-1].endswith("word499")


async def test_a_long_question_searches_with_the_average_of_its_pieces(embedded):
    embedded("", chunk_size=100)
    await get_context_from_opensearch("collection-1", 5, LONG)
    vector = searched_vector(embedded)
    # the pieces alternate between two directions, so the average lies between them and is still
    # the unit length every other query vector has
    assert len(vector) == 2
    assert vector[0] > 0 and vector[1] > 0
    assert math.isclose(math.hypot(*vector), 1.0)
