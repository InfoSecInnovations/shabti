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


def empty_response():
    return {"hits": {"hits": []}}


class Client:
    """An index with nothing in it unless a test says otherwise, so retrieval stops after the search."""

    def __init__(self):
        self.searches = []
        self.msearches = []
        self.mgets = []
        # what a search and each leg of a multi-search find, as the index returns them
        self.search_hits = []
        self.msearch_responses = [empty_response(), empty_response()]

    async def search(self, body, index):
        self.searches.append(body)
        return {"hits": {"hits": self.search_hits}}

    async def msearch(self, body):
        self.msearches.append(body)
        return {"responses": self.msearch_responses}

    async def mget(self, body):
        self.mgets.append(body)
        return {
            "docs": [
                {"_id": doc["_id"], "found": True, "_source": {"name": doc["_id"]}}
                for doc in body["docs"]
            ]
        }


def hit(text, score=1.0, doc="doc-1"):
    """A vector child as the index returns it, found at `score`."""
    return {
        "_score": score,
        "_source": {
            "page_id": f"page-{text}",
            "text": text,
            "text_hash": text,
            "child_item_to_document": {"parent": doc},
        },
    }


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

        async def get_document_counts(collection_id, doc_ids):
            return {doc_id: {"page_count": 1, "vector_count": 1} for doc_id in doc_ids}

        monkeypatch.setattr(
            opensearch_prompting, "get_document_counts", get_document_counts
        )
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


async def test_by_default_retrieval_is_one_dense_search_of_the_reference_limit(
    embedded,
):
    embedded("")
    client = embedded.client
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert client.msearches == []
    assert len(client.searches) == 1
    query = client.searches[0]
    assert query["size"] == 5
    knn = query["query"]["knn"]["document_vector"]
    assert knn["min_score"] == opensearch_prompting.MIN_SCORE
    assert query["collapse"] == {"field": "text_hash"}
    assert "text_hash" in query["_source"]["includes"]


async def test_hybrid_mode_sends_a_dense_and_a_keyword_search_in_one_msearch(
    embedded, monkeypatch
):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("SHABTI_RETRIEVAL_CANDIDATES", "20")
    embedded("")
    client = embedded.client
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert client.searches == []
    assert len(client.msearches) == 1
    dense_header, dense, keyword_header, keyword = client.msearches[0]
    assert dense_header == {"index": "collection-1"}
    assert keyword_header == {"index": "collection-1"}
    assert "knn" in dense["query"]
    assert dense["size"] == 20
    assert keyword["size"] == 20
    assert keyword["query"]["bool"]["must"] == [
        {"match": {"text": "what is a shabti?"}}
    ]
    assert keyword["query"]["bool"]["filter"] == [{"term": {"type": "vector"}}]


async def test_an_ingesting_document_is_excluded_from_both_legs(embedded, monkeypatch):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    embedded("")
    client = embedded.client

    # after the fixture's own stub, which would otherwise replace this one
    async def get_ingesting_document_ids(collection_id):
        return ["doc-9"]

    monkeypatch.setattr(
        opensearch_prompting, "get_ingesting_document_ids", get_ingesting_document_ids
    )
    await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    _, dense, _, keyword = client.msearches[0]
    assert dense["query"]["knn"]["document_vector"]["filter"] == {
        "bool": {"must_not": [{"terms": {"doc_id": ["doc-9"]}}]}
    }
    assert keyword["query"]["bool"]["must_not"] == [{"terms": {"doc_id": ["doc-9"]}}]


async def test_hybrid_with_an_empty_vector_leg_and_no_reranker_has_no_sources(
    embedded, monkeypatch
):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    embedded("")
    client = embedded.client
    client.msearch_responses = [
        empty_response(),
        {"hits": {"hits": [hit("keyword only")]}},
    ]
    result = await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert result == {"context": "", "sources": []}


async def test_a_failed_leg_of_a_hybrid_search_is_an_error(embedded, monkeypatch):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    embedded("")
    client = embedded.client
    client.msearch_responses = [
        empty_response(),
        {"error": {"type": "search_phase_execution_exception"}},
    ]
    with pytest.raises(RuntimeError):
        await get_context_from_opensearch("collection-1", 5, "what is a shabti?")


async def test_rrf_puts_a_chunk_found_by_both_legs_first(embedded, monkeypatch):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    embedded("")
    client = embedded.client
    client.msearch_responses = [
        {"hits": {"hits": [hit("dense only"), hit("both")]}},
        {"hits": {"hits": [hit("both"), hit("keyword only")]}},
    ]
    result = await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert [source["text"] for source in result["sources"]] == [
        "both",
        "dense only",
        "keyword only",
    ]


async def test_min_max_with_all_weight_on_the_dense_leg_keeps_the_dense_order(
    embedded, monkeypatch
):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("SHABTI_HYBRID_FUSION", "min_max")
    monkeypatch.setenv("SHABTI_HYBRID_DENSE_WEIGHT", "1")
    embedded("")
    client = embedded.client
    client.msearch_responses = [
        {"hits": {"hits": [hit("a", 3.0), hit("b", 2.0), hit("c", 1.0)]}},
        {"hits": {"hits": [hit("c", 9.0), hit("b", 5.0)]}},
    ]
    result = await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert [source["text"] for source in result["sources"]] == ["a", "b", "c"]


async def test_reranking_reorders_sources_and_drops_those_below_the_floor(
    embedded, monkeypatch
):
    monkeypatch.setenv("SHABTI_RERANK_MODEL", "local/reranker")
    monkeypatch.setenv("SHABTI_RERANK_MIN_SCORE", "0.5")
    embedded("")
    client = embedded.client
    client.search_hits = [hit("a"), hit("b"), hit("c")]
    calls = []

    def fake_rerank(query, documents, model_id):
        calls.append((query, documents, model_id))
        return [0.2, 0.9, 0.7]

    monkeypatch.setattr(opensearch_prompting, "rerank", fake_rerank)
    result = await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
    assert calls == [("what is a shabti?", ["a", "b", "c"], "local/reranker")]
    assert [source["text"] for source in result["sources"]] == ["b", "c"]


async def test_dense_reranking_over_fetches_and_returns_at_most_the_limit(
    embedded, monkeypatch
):
    monkeypatch.setenv("SHABTI_RERANK_MODEL", "local/reranker")
    monkeypatch.setenv("SHABTI_RETRIEVAL_CANDIDATES", "20")
    embedded("")
    client = embedded.client
    client.search_hits = [hit("a"), hit("b"), hit("c")]
    monkeypatch.setattr(
        opensearch_prompting,
        "rerank",
        lambda query, documents, model_id: [0.0, 0.1, 0.2],
    )
    result = await get_context_from_opensearch("collection-1", 2, "what is a shabti?")
    assert client.searches[0]["size"] == 20
    assert [source["text"] for source in result["sources"]] == ["c", "b"]


async def test_an_unknown_retrieval_mode_is_refused(embedded, monkeypatch):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "sparse")
    embedded("")
    with pytest.raises(ValueError):
        await get_context_from_opensearch("collection-1", 5, "what is a shabti?")
