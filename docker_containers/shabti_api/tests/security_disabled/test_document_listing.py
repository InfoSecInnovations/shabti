"""Listing a collection's documents through the query string.

The listing route is what the document management UI is built on: its type filter, its pager and
its sort all arrive as query parameters, and the two counts it returns drive two different parts of
the screen. Everything here goes over HTTP rather than calling `get_documents` directly, because
the parameter binding - a repeated `filter_document_type` in particular - is part of what is being
checked.

The corpus is built once for the module. Ingesting four documents means parsing and embedding them,
which is far too slow to repeat per test.
"""

import os
import secrets

import pytest_asyncio

from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
)

assets = os.path.join(os.path.dirname(__file__), "..", "assets")
# "ingest" appears in test_doc_2.txt and in nothing else here, so a search for it has exactly one
# right answer, and one that can only have come from the document's contents
searchable = "test_doc_2.txt"
search_term = "ingest"
# an HTML page, purely to put a third media type in the collection. it deliberately shares no
# vocabulary with the other three, so it can never be the answer to a content search below
html_page = """<html><head><title>Basalt</title></head>
<body><p>Basalt forms where lava cools quickly at the surface.</p></body></html>"""


class Corpus:
    def __init__(self, collection_id, documents):
        self.collection_id = collection_id
        self.documents = documents

    @property
    def ids(self):
        return {document["document_id"] for document in self.documents}

    @property
    def types(self):
        return {document["media_type"] for document in self.documents}

    def ids_of(self, *types):
        return {
            document["document_id"]
            for document in self.documents
            if document["media_type"] in types
        }

    def document(self, filename):
        return next(
            document
            for document in self.documents
            if document.get("filename") == filename
        )

    def other_type(self, filename):
        """A media type present in the collection that this document is not."""
        return next(
            media_type
            for media_type in sorted(self.types)
            if media_type != self.document(filename)["media_type"]
        )


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def corpus(shabti_client, ingest_and_wait, tmp_path_factory):
    collection = await create_collection(None, secrets.token_hex(8))
    collection_id = collection.collection_id
    page = tmp_path_factory.mktemp("listing") / "basalt.html"
    page.write_text(html_page)
    paths = [
        os.path.join(assets, "test_doc.txt"),
        os.path.join(assets, searchable),
        os.path.join(assets, "prompt_test.md"),
        str(page),
    ]
    # one ingest at a time, so the documents land milliseconds apart rather than racing for the
    # same timestamp: the sort and paging assertions below need a total order to exist
    for path in paths:
        with open(path, "rb") as f:
            response, ingest, _ = ingest_and_wait(
                "POST",
                f"/collections/{collection_id}/documents/files",
                files=[("files", f)],
            )
        assert response.status_code == 201, response.text
        assert not [item for item in ingest.items if item.error], [
            (item.label, item.error.message) for item in ingest.items if item.error
        ]
    listing_response = shabti_client.get(f"/collections/{collection_id}/documents")
    documents = listing_response.json()["documents"]
    assert len(documents) == len(paths)
    dates = [document["ingest_date"] for document in documents]
    # asserted here rather than left to trip up one sort assertion at random: if two documents did
    # land on the same millisecond, their relative order is genuinely undefined
    assert len(set(dates)) == len(dates), f"documents share an ingest date: {dates}"
    built = Corpus(collection_id, documents)
    # the type filter tests need something to tell apart, and Tika decides the types rather than
    # this file does, so check there is more than one before relying on it
    assert len(built.types) > 1, built.types
    yield built
    await delete_collection(None, collection_id)


def listing(shabti_client, corpus, **params):
    response = shabti_client.get(
        f"/collections/{corpus.collection_id}/documents", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return {document["document_id"] for document in result["documents"]}


def ordered_ids(result):
    return [document["document_id"] for document in result["documents"]]


async def test_an_unfiltered_listing_returns_the_whole_collection(
    shabti_client, corpus
):
    result = listing(shabti_client, corpus)
    assert ids(result) == corpus.ids
    assert result["total_hits"] == len(corpus.documents)
    assert result["total_documents"] == len(corpus.documents)


async def test_filtering_by_each_type_returns_exactly_that_type(shabti_client, corpus):
    # the types come from Tika at ingest time, so they are looped over rather than parametrized:
    # there is nothing to parametrize with until the corpus exists
    for media_type in sorted(corpus.types):
        result = listing(shabti_client, corpus, filter_document_type=[media_type])
        assert ids(result) == corpus.ids_of(media_type), media_type
        assert {document["media_type"] for document in result["documents"]} == {
            media_type
        }
        assert result["total_hits"] == len(corpus.ids_of(media_type)), media_type
        # the collection has not changed size, and the label counting it must not move
        assert result["total_documents"] == len(corpus.documents)


async def test_filtering_by_two_types_returns_both(shabti_client, corpus):
    first, second = sorted(corpus.types)[:2]
    result = listing(shabti_client, corpus, filter_document_type=[first, second])
    assert ids(result) == corpus.ids_of(first, second)


async def test_filtering_by_every_type_returns_everything(shabti_client, corpus):
    result = listing(shabti_client, corpus, filter_document_type=sorted(corpus.types))
    assert ids(result) == corpus.ids


async def test_filtering_by_an_absent_type_returns_nothing(shabti_client, corpus):
    result = listing(
        shabti_client, corpus, filter_document_type=["application/x-not-here"]
    )
    assert result["documents"] == []
    assert result["total_hits"] == 0
    assert result["total_documents"] == len(corpus.documents)


async def test_searching_matches_document_contents(shabti_client, corpus):
    result = listing(shabti_client, corpus, search=search_term)
    assert ordered_ids(result) == [corpus.document(searchable)["document_id"]]


async def test_searching_matches_a_filename(shabti_client, corpus):
    result = listing(shabti_client, corpus, search="prompt_test")
    assert ordered_ids(result) == [corpus.document("prompt_test.md")["document_id"]]


async def test_searching_matches_a_document_id(shabti_client, corpus):
    document_id = corpus.document("test_doc.txt")["document_id"]
    result = listing(shabti_client, corpus, search=document_id)
    assert ordered_ids(result) == [document_id]


async def test_a_search_and_a_type_filter_apply_together(shabti_client, corpus):
    found = corpus.document(searchable)
    result = listing(
        shabti_client,
        corpus,
        search=search_term,
        filter_document_type=[found["media_type"]],
    )
    assert ordered_ids(result) == [found["document_id"]]


async def test_a_type_filter_still_excludes_a_search_match(shabti_client, corpus):
    # a search builds a different query altogether, and applies the type filter by replacing the
    # one already there rather than adding to it, so it needs proving on this path of its own
    result = listing(
        shabti_client,
        corpus,
        search=search_term,
        filter_document_type=[corpus.other_type(searchable)],
    )
    assert result["documents"] == []


async def test_a_filtered_search_returns_only_whole_documents(shabti_client, corpus):
    # the clause restricting results to documents lives inside the search query rather than beside
    # it, so a filtered search is where a chunk or a page could leak out looking like a document
    result = listing(
        shabti_client, corpus, search="e", filter_document_type=sorted(corpus.types)
    )
    assert ids(result) <= corpus.ids
    assert all(document["media_type"] for document in result["documents"])


async def test_max_results_limits_the_page_not_the_counts(shabti_client, corpus):
    result = listing(shabti_client, corpus, max_results=2)
    assert len(result["documents"]) == 2
    # the pager is drawn from total_hits, so truncating the page must not shrink it
    assert result["total_hits"] == len(corpus.documents)
    assert result["total_documents"] == len(corpus.documents)


async def test_paging_walks_the_whole_collection(shabti_client, corpus):
    first = listing(shabti_client, corpus, max_results=2, page=0, sort="date_asc")
    second = listing(shabti_client, corpus, max_results=2, page=1, sort="date_asc")
    assert not ids(first) & ids(second)
    assert ids(first) | ids(second) == corpus.ids
    assert ordered_ids(first) + ordered_ids(second) == ordered_ids(
        listing(shabti_client, corpus, sort="date_asc")
    )


async def test_sorting_by_date(shabti_client, corpus):
    ascending = listing(shabti_client, corpus, sort="date_asc")
    dates = [document["ingest_date"] for document in ascending["documents"]]
    assert dates == sorted(dates)
    descending = listing(shabti_client, corpus, sort="date_desc")
    assert ordered_ids(descending) == list(reversed(ordered_ids(ascending)))


async def test_an_unrecognised_sort_is_ignored(shabti_client, corpus):
    # the UI sends "relevance" for its default ordering, which the query has no clause for
    result = listing(shabti_client, corpus, sort="relevance")
    assert ids(result) == corpus.ids


async def test_a_page_without_a_page_size_is_ignored(shabti_client, corpus):
    # there is no offset to apply without knowing how big a page is, so nothing is skipped
    result = listing(shabti_client, corpus, page=1)
    assert ids(result) == corpus.ids


async def test_document_types_lists_what_the_filter_can_be_set_to(
    shabti_client, corpus
):
    response = shabti_client.get(f"/collections/{corpus.collection_id}/document_types")
    assert response.status_code == 200
    assert set(response.json()) == corpus.types
