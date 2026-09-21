"""The prefix a model wants on a stored chunk is model input and nothing else.

It goes in front of the text on its way to the embeddings server, and must not reach the text kept
in the index: that text is what gets quoted back to someone as a reference, and its hash is what
stops the same chunk taking more than one slot in an answer's reference window.
"""

import pytest

from ...src.app.functionality import opensearch_ingesting
from ...src.app.functionality.loaders.base_loader import (
    ShabtiDocument,
    page_list_stream,
)
from ...src.app.functionality.opensearch_ingesting import insert


class Client:
    """Enough of OpenSearch to get a one page document written."""

    def __init__(self):
        self.ids = 0

    async def index(self, index, body, **kwargs):
        self.ids += 1
        return {"_id": f"id-{self.ids}"}

    async def update(self, **kwargs):
        return {}

    class Indices:
        async def refresh(self, index):
            return {}

    indices = Indices()


def page(content):
    return ShabtiDocument.ShabtiPage(
        metadata=ShabtiDocument.ShabtiPage.PageMetadata(page_number=1), content=content
    )


def stream(content):
    metadata = ShabtiDocument.DocumentMetadata(
        media_type="text/plain",
        source="a-doc.txt",
        ingest_date=0,
        filename="a-doc.txt",
        languages=["en"],
    )
    return page_list_stream(metadata, [page(content)])


@pytest.fixture
def ingest(monkeypatch):
    """Runs one page through `insert`, reporting what was embedded and what was written."""
    embedded = []
    written = []

    def configure(document_prefix):
        def create_embeddings(text, model_id=None):
            embedded.extend(text)
            return [[0.1] for _ in text]

        async def async_bulk(client, actions):
            written.extend(actions)
            return len(written), []

        monkeypatch.setattr(opensearch_ingesting, "get_client", Client)
        monkeypatch.setattr(
            opensearch_ingesting, "get_embeddings_model_id", lambda: "embed-1"
        )
        monkeypatch.setattr(opensearch_ingesting, "chunk_size", lambda _: 200)
        monkeypatch.setattr(opensearch_ingesting, "context_limit", lambda _: 512)
        monkeypatch.setattr(
            opensearch_ingesting, "document_prefix", lambda _: document_prefix
        )
        monkeypatch.setattr(
            opensearch_ingesting, "count_tokens", lambda text, _: len(text.split())
        )
        monkeypatch.setattr(
            opensearch_ingesting, "create_embeddings", create_embeddings
        )
        monkeypatch.setattr(opensearch_ingesting, "async_bulk", async_bulk)

        async def check_embeddings_model(collection_id, model_id):
            return None

        async def find_duplicate_document(*args):
            return None

        monkeypatch.setattr(
            opensearch_ingesting, "check_embeddings_model", check_embeddings_model
        )
        monkeypatch.setattr(
            opensearch_ingesting, "find_duplicate_document", find_duplicate_document
        )
        return embedded, written

    return configure


async def drain(content):
    async for _ in insert("collection-1", stream(content)):
        pass


async def test_a_document_prefix_reaches_the_model_but_not_the_index(ingest):
    embedded, written = ingest("search_document: ")
    await drain("the shabti answers for its owner")

    assert embedded == ["search_document: the shabti answers for its owner"]
    # what someone gets quoted, and what collapsing an answer's duplicates depends on
    assert [action["text"] for action in written] == [
        "the shabti answers for its owner"
    ]


async def test_the_stored_hash_is_of_the_text_as_written(ingest):
    # hashing the prefixed text would still dedupe, but only against chunks embedded by a model
    # configured the same way, so the hash tracks the text rather than the model
    _, written = ingest("search_document: ")
    await drain("the shabti answers for its owner")
    ingest("")
    await drain("the shabti answers for its owner")
    # the same text ingested by a prefixed model and an unprefixed one, collapsing as one chunk
    assert len({action["text_hash"] for action in written}) == 1


async def test_a_model_wanting_no_prefix_embeds_the_chunk_as_written(ingest):
    embedded, written = ingest("")
    await drain("the shabti answers for its owner")
    assert embedded == ["the shabti answers for its owner"]
    assert [action["text"] for action in written] == embedded
