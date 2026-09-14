"""A document still being written: out of the listing, and out of an answer.

The parent row is indexed on the first page, so a document exists - and once the index refreshes,
is visible - long before its ingest finishes. It showed up in the collection management UI at that
point with a partial vector count and then again with the real one, and its chunks could be quoted
as references for a document that had not finished and might still be rolled back.

Mid-ingest state is reached by driving `insert_document` by hand rather than going through the
route: for a single page file the first value it yields lands after the page and its vectors are
written and before the document is marked finished, which is exactly the state that was showing.
Nothing in an ingest refreshes until it ends, so each assertion refreshes explicitly rather than
waiting out the index's own interval.
"""

from contextlib import asynccontextmanager

from ...src.app.functionality.ingesting import insert_document
from ...src.app.functionality.loading import load_file
from ...src.app.functionality.opensearch import get_client, sweep_ingesting_documents
from ...src.app.functionality.opensearch_prompting import get_context_from_opensearch

# three ways of saying the same thing, so all three clear the similarity floor for one query, and
# the one being ingested - which the query is a copy of - is the closest match of them
kingfisher = (
    "A kingfisher is a small bright blue bird that dives for fish from a perch above "
    "slow moving water."
)
perched = (
    "Kingfishers are small brightly coloured birds which watch for fish from a perch "
    "over slow water."
)
dives = (
    "The kingfisher hunts by dropping from a low perch into slow moving water to catch "
    "small fish."
)


def stream_of(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    with open(path, "rb") as f:
        return load_file(f, name)


async def ingest(collection_id, tmp_path, name, text) -> str:
    last = None
    async for info in insert_document(
        None, collection_id, stream_of(tmp_path, name, text)
    ):
        last = info
    return last.document_id


@asynccontextmanager
async def half_ingested(collection_id, tmp_path, name, text):
    """Hold an ingest at the point its page is written and its document is not finished."""
    pages = insert_document(None, collection_id, stream_of(tmp_path, name, text))
    first = await anext(pages)
    assert not first.complete
    await get_client().indices.refresh(index=collection_id)
    yield first
    # drained rather than closed: a close skips the rollback a throw would run, and this document
    # is meant to survive so the assertions after the block have something to find
    last = first
    async for info in pages:
        last = info
    assert last.complete


async def test_a_document_being_ingested_is_not_listed(
    shabti_client, shabti_collection_id, tmp_path
):
    documents = f"/collections/{shabti_collection_id}/documents"
    document_types = f"/collections/{shabti_collection_id}/document_types"
    async with half_ingested(
        shabti_collection_id, tmp_path, "kingfisher.txt", kingfisher
    ) as info:
        listing = shabti_client.get(documents).json()
        assert listing["documents"] == [], listing
        # the count the UI pages on, which has to agree with the page it is counting
        assert listing["total_documents"] == 0, listing
        assert listing["total_hits"] == 0, listing
        # a different query branch, and the one a user typing into the filter box takes
        searched = shabti_client.get(documents, params={"search": "kingfisher"}).json()
        assert searched["documents"] == [], searched
        # a type only an unfinished document has would otherwise be offered as a filter that
        # matches nothing
        assert shabti_client.get(document_types).json() == []

    listing = shabti_client.get(documents).json()
    assert [doc["document_id"] for doc in listing["documents"]] == [info.document_id]
    # the number that used to appear as 0 first and then jump once the ingest finished
    assert listing["documents"][0]["vector_count"] > 0, listing
    assert listing["total_documents"] == 1, listing


async def test_a_document_being_ingested_is_not_a_source(
    shabti_collection_id, tmp_path
):
    async with half_ingested(
        shabti_collection_id, tmp_path, "kingfisher.txt", kingfisher
    ) as info:
        vectors = await get_client().search(
            body={
                "size": 0,
                "query": {"bool": {"filter": [{"term": {"doc_id": info.document_id}}]}},
            },
            index=shabti_collection_id,
        )
        # asserted rather than assumed: the chunks are searchable at this point, so what keeps
        # them out of the answer below is the exclusion and not there being nothing to find
        assert vectors["hits"]["total"]["value"] > 0, vectors

        context = await get_context_from_opensearch(shabti_collection_id, 5, kingfisher)
        assert context["sources"] == [], context["sources"]
        assert context["context"] == ""

    context = await get_context_from_opensearch(shabti_collection_id, 5, kingfisher)
    assert [source["doc_metadata"]["document_id"] for source in context["sources"]] == [
        info.document_id
    ]


async def test_a_document_being_ingested_costs_no_references(
    shabti_collection_id, tmp_path
):
    finished = {
        await ingest(shabti_collection_id, tmp_path, "perched.txt", perched),
        await ingest(shabti_collection_id, tmp_path, "dives.txt", dives),
    }
    # first, so that a paraphrase drifting under the similarity floor fails here rather than
    # looking like the exclusion below having taken a reference with it
    context = await get_context_from_opensearch(shabti_collection_id, 2, kingfisher)
    assert len(context["sources"]) == 2, context["sources"]

    async with half_ingested(
        shabti_collection_id, tmp_path, "kingfisher.txt", kingfisher
    ) as info:
        context = await get_context_from_opensearch(shabti_collection_id, 2, kingfisher)
        cited = {source["doc_metadata"]["document_id"] for source in context["sources"]}
        # the whole reason the exclusion is in the query rather than applied to its results: the
        # document being written is the closest match of the three, so dropping its hit after the
        # search would leave the answer a reference short of what was asked for
        assert cited == finished, cited
        assert info.document_id not in cited


async def test_a_document_ingested_before_the_flag_existed_is_still_listed(
    shabti_client, shabti_collection_id, tmp_path
):
    document_id = await ingest(
        shabti_collection_id, tmp_path, "kingfisher.txt", kingfisher
    )
    await get_client().update(
        index=shabti_collection_id,
        id=document_id,
        body={"script": {"source": "ctx._source.remove('ingesting')"}},
        refresh=True,
    )
    listing = shabti_client.get(f"/collections/{shabti_collection_id}/documents").json()
    # the flag says "unfinished" rather than "finished" precisely so that a collection filled
    # before it existed, where no document carries either, is not emptied by the filter
    assert [doc["document_id"] for doc in listing["documents"]] == [document_id]
    context = await get_context_from_opensearch(shabti_collection_id, 5, kingfisher)
    assert [source["doc_metadata"]["document_id"] for source in context["sources"]] == [
        document_id
    ]


async def test_a_document_left_unfinished_is_swept(shabti_collection_id, tmp_path):
    client = get_client()
    kept = await ingest(shabti_collection_id, tmp_path, "kingfisher.txt", kingfisher)
    # what a container killed mid-ingest leaves behind: a parent flagged unfinished with children
    # hanging off it, and nothing left in the process that knows it is there. hidden from every
    # listing by that flag, so without this nothing could ever reach it again
    orphan = (
        await client.index(
            index=shabti_collection_id,
            body={
                "type": "document",
                "child_item_to_document": "document",
                "ingesting": True,
                "media_type": "text/plain",
                "source": "orphan.txt",
                "filename": "orphan.txt",
                "ingest_date": 1,
                "languages": ["en"],
            },
        )
    )["_id"]
    await client.index(
        index=shabti_collection_id,
        body={
            "child_item_to_document": {"name": "child_item", "parent": orphan},
            "type": "page",
            "page_number": None,
            "source": None,
        },
        routing=orphan,
        refresh=True,
    )

    assert orphan in await sweep_ingesting_documents()
    assert not await client.exists(index=shabti_collection_id, id=orphan)
    pages = await client.search(
        body={"size": 0, "query": {"bool": {"filter": [{"term": {"type": "page"}}]}}},
        index=shabti_collection_id,
    )
    # the orphan's page went with it, and the finished document kept its own
    assert pages["hits"]["total"]["value"] == 1, pages
    assert await client.exists(index=shabti_collection_id, id=kept)
