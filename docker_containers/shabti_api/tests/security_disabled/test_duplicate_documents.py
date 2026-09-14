"""Duplicate content: kept out of a collection at ingest, and out of an answer at query time.

The two halves are deliberately tested separately. Retrieval has to cope with a collection that
already holds the same chunk twice however it got there - shared boilerplate, a revised document,
or a duplicate the ingest check could not see - so those tests put one there on purpose rather than
relying on the ingest check having failed.

`get_context_from_opensearch` is called directly rather than through `/prompt` because what is
being asserted is which references the search chose, and the answer written from them is explicitly
not deterministic.
"""

import os
import secrets

import pytest
import pytest_asyncio
from shabti_types import DuplicateDocumentError

from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
)
from ...src.app.functionality.ingesting import insert_document
from ...src.app.functionality.loading import load_file
from ...src.app.functionality.opensearch import get_client
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch

assets = os.path.join(os.path.dirname(__file__), "..", "assets")
# the text of test_doc.txt, which is one chunk, so a query close to it has exactly one right answer
# per copy of the document
duplicated = "test_doc.txt"
duplicated_query = "This is not a real document, it's just a test."
distinct = "test_doc_2.txt"


def ingest_file(ingest_and_wait, collection_id, filename):
    with open(os.path.join(assets, filename), "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{collection_id}/documents/files",
            files=[("files", f)],
        )
    return response, ingest


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def duplicated_collection(shabti_client, ingest_and_wait):
    """A collection holding one chunk twice, plus a different chunk once.

    The second copy is written straight to OpenSearch rather than ingested, because ingesting it is
    what the other half of this file asserts is refused. Copying the vector child verbatim is the
    point: identical text embeds to an identical vector, so this is exactly the state a duplicate
    would have left behind.
    """
    collection = await create_collection(None, secrets.token_hex(8))
    collection_id = collection.collection_id
    for filename in (duplicated, distinct):
        response, ingest = ingest_file(ingest_and_wait, collection_id, filename)
        assert response.status_code == 201, response.text
        assert not [item for item in ingest.items if item.error]

    client = get_client()
    original = (
        await client.search(
            body={
                "size": 1,
                "query": {"bool": {"filter": [{"term": {"type": "vector"}}]}},
            },
            index=collection_id,
        )
    )["hits"]["hits"][0]
    copy_id = (
        await client.index(
            index=collection_id,
            body={
                "type": "document",
                "child_item_to_document": "document",
                "media_type": "text/plain",
                "source": duplicated,
                "filename": duplicated,
                "ingest_date": 1,
                "languages": ["en"],
            },
        )
    )["_id"]
    page_id = (
        await client.index(
            index=collection_id,
            body={
                "child_item_to_document": {"name": "child_item", "parent": copy_id},
                "type": "page",
                "page_number": None,
                "source": None,
            },
            routing=copy_id,
        )
    )["_id"]
    await client.index(
        index=collection_id,
        body={
            **original["_source"],
            "child_item_to_document": {"name": "child_item", "parent": copy_id},
            "page_id": page_id,
            "doc_id": copy_id,
        },
        routing=copy_id,
        refresh=True,
    )
    yield collection_id
    await delete_collection(None, collection_id)


# long enough to split into more than one chunk, and all of it about the same thing, so both of
# its chunks are plausible answers to one query
long_document = "\n\n".join(
    [
        "Basalt is a fine grained igneous rock that forms where lava reaches the surface "
        "and cools too quickly for large crystals to grow. It is the most common volcanic "
        "rock on the planet, and most of the ocean floor is made of it. Its dark colour "
        "comes from the iron and magnesium rich minerals it is largely composed of.",
        "Where a thick basalt flow cools slowly and evenly it can contract into columns, "
        "usually with five or six sides, standing perpendicular to the surface that was "
        "cooling. The columns at the Giant's Causeway and at Fingal's Cave formed this "
        "way, and the same jointing appears wherever a flow is deep enough to cool from "
        "the top and the bottom at once over a long enough period of time.",
    ]
)


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def multi_chunk_collection(shabti_client, ingest_and_wait, tmp_path_factory):
    """A collection holding exactly one document, which is more than one chunk."""
    collection = await create_collection(None, secrets.token_hex(8))
    collection_id = collection.collection_id
    path = tmp_path_factory.mktemp("chunks") / "basalt.txt"
    path.write_text(long_document)
    with open(path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201, response.text
    documents = shabti_client.get(f"/collections/{collection_id}/documents").json()
    # asserted rather than assumed: the splitter decides this, and a change to the chunk capacity
    # would otherwise turn the test below into one that passes without checking anything
    assert documents["documents"][0]["vector_count"] > 1, documents
    yield collection_id
    await delete_collection(None, collection_id)


async def test_different_chunks_of_one_page_are_not_collapsed_together(
    multi_chunk_collection,
):
    context = await get_context_from_opensearch(
        multi_chunk_collection, 2, "What is basalt and how does it form columns?"
    )
    # the collection holds one document and a plain text file is a single page, so two references
    # can only be two different chunks of that one page. collapsing on the page rather than on the
    # text would leave one: a page's chunks are different pieces of context, all worth having
    assert len(context["sources"]) == 2, context["sources"]
    references = context["context"].split("\n")
    assert len(references) == len(set(references)), references


async def test_a_chunk_held_twice_takes_one_reference_not_two(duplicated_collection):
    context = await get_context_from_opensearch(
        duplicated_collection, 2, duplicated_query
    )
    # one line per reference, because every document here is a single chunk
    references = context["context"].split("\n")
    assert len(references) == len(set(references)), references


async def test_a_duplicate_does_not_crowd_out_another_document(duplicated_collection):
    context = await get_context_from_opensearch(
        duplicated_collection, 2, duplicated_query
    )
    filenames = {source["doc_metadata"]["filename"] for source in context["sources"]}
    # both references would otherwise be the two copies of the same chunk, and the only other
    # document in the collection would never be seen
    assert distinct in filenames, filenames


async def test_the_same_file_uploaded_twice_is_refused(
    shabti_client, ingest_and_wait, shabti_collection_id
):
    response, ingest = ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    assert response.status_code == 201, response.text
    assert not [item for item in ingest.items if item.error]

    response, ingest = ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    assert response.status_code == 201, response.text
    errors = [item.error for item in ingest.items if item.error]
    assert [error.error for error in errors] == ["DuplicateDocumentError"]
    # the message is where the surviving document's id has to travel, since an ingest item's error
    # has no field of its own for it
    assert errors[0].message

    documents = shabti_client.get(
        f"/collections/{shabti_collection_id}/documents"
    ).json()
    assert documents["total_documents"] == 1


async def test_a_refused_upload_is_never_embedded(
    ingest_and_wait, shabti_collection_id
):
    ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    _, ingest = ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    # the id it would have been given already exists, so the copy is turned away before it is
    # parsed and before a single chunk is embedded - which is the whole reason for hashing the
    # bytes rather than only the extracted text
    assert not [item for item in ingest.items if item.info]


async def test_a_refused_upload_leaves_no_binary_behind(
    ingest_and_wait, shabti_collection_id
):
    files_dir = os.getenv("SHABTI_FILES_DIR")
    before = set(os.listdir(files_dir))
    ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    # the document that was kept owns its binary from here, so this is the file that has to still
    # be there once the copy has been turned away
    kept = set(os.listdir(files_dir)) - before
    assert len(kept) == 1

    ingest_file(ingest_and_wait, shabti_collection_id, duplicated)
    # the refused upload was staged to disk by the POST before anything knew it was a copy, so
    # nothing but the reader's cleanup stops it being left there for ever
    assert set(os.listdir(files_dir)) - before == kept


async def test_two_different_files_are_both_ingested(
    shabti_client, ingest_and_wait, shabti_collection_id
):
    for filename in (duplicated, distinct):
        response, ingest = ingest_file(ingest_and_wait, shabti_collection_id, filename)
        assert response.status_code == 201, response.text
        assert not [item for item in ingest.items if item.error]

    documents = shabti_client.get(
        f"/collections/{shabti_collection_id}/documents"
    ).json()
    assert documents["total_documents"] == 2


async def test_the_same_content_with_no_binary_to_compare_is_refused(
    shabti_client, shabti_collection_id
):
    """The content hash, which covers what an id made of the uploaded bytes cannot.

    Ingested straight through `insert_document` with no binary hash, which is the shape a crawled
    document has: there are no bytes to give it a deterministic id, so the check on the way out -
    embed the document, find it is a copy, roll it back - is the only thing that can refuse it.
    """
    # loaded up front, and twice: a page stream is consumed once, so the second ingest needs its
    # own rather than a rewind of the first
    streams = []
    for _ in range(2):
        with open(os.path.join(assets, duplicated), "rb") as f:
            streams.append(load_file(f, duplicated))

    async for _ in insert_document(None, shabti_collection_id, streams[0]):
        pass
    with pytest.raises(DuplicateDocumentError):
        async for _ in insert_document(None, shabti_collection_id, streams[1]):
            pass

    documents = shabti_client.get(
        f"/collections/{shabti_collection_id}/documents"
    ).json()
    # the copy rolled itself back, so its pages and vectors went with it
    assert documents["total_documents"] == 1
