"""Hybrid retrieval against the real stack.

An identifier is the case dense retrieval is weakest at: a made-up code like `QX-7731-B` embeds to
nothing in particular, so only the keyword leg can find the chunk holding it. This test proves that
leg is wired in.

`get_context_from_opensearch` is called directly for the same reason as in test_duplicate_documents:
what is asserted is which references the search chose, not an answer written from them.
"""

import secrets

import pytest_asyncio

from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
)
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch

identifier = "QX-7731-B"

# the identifier sits inside an otherwise unrelated sentence, so nothing but its exact token links
# the query to this chunk. the other two are on clearly different topics
documents = {
    "maintenance.txt": (
        "Maintenance note: the pump in the east cellar was replaced on Tuesday under work "
        f"order {identifier}, and the old unit has been sent for recycling."
    ),
    "gardening.txt": (
        "Tomatoes need full sun and regular watering. Pinch out the side shoots to encourage "
        "a single strong stem."
    ),
    "history.txt": (
        "The Roman road network connected provincial towns to the capital, and milestones "
        "along it recorded distances in miles."
    ),
}


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def identifier_collection(shabti_client, ingest_and_wait, tmp_path_factory):
    """A collection holding three small documents, one of which contains the identifier."""
    collection = await create_collection(None, secrets.token_hex(8))
    collection_id = collection.collection_id
    directory = tmp_path_factory.mktemp("hybrid")
    paths = []
    for name, text in documents.items():
        path = directory / name
        path.write_text(text)
        paths.append(path)
    files = [("files", open(path, "rb")) for path in paths]
    try:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{collection_id}/documents/files",
            files=files,
        )
    finally:
        for _, f in files:
            f.close()
    assert response.status_code == 201, response.text
    assert not [item for item in ingest.items if item.error], ingest.items
    yield collection_id
    await delete_collection(None, collection_id)


async def test_hybrid_retrieval_finds_an_identifier_only_query(
    identifier_collection, monkeypatch
):
    monkeypatch.setenv("SHABTI_RETRIEVAL_MODE", "hybrid")
    context = await get_context_from_opensearch(identifier_collection, 5, identifier)
    assert context["sources"], context
    # the dense leg has nothing to offer for a bare code, so this only passes if the keyword leg
    # found the chunk and fusion kept it at the top
    assert identifier in context["sources"][0]["text"], context["sources"][0]
