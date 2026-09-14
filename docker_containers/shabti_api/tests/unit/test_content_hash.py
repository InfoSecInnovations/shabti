"""The two properties the document hash is relied on for, and the one the chunk hash is.

Nothing here talks to OpenSearch: these are the pure functions the ingest path and the prompting
query both build on, and the point of testing them separately is that a change to either digest is
silent everywhere else until documents stop matching each other.
"""

from ...src.app.functionality.content_hash import (
    binary_hasher,
    chunk_hash,
    document_hasher,
)


def hash_pages(*pages: str) -> str:
    hasher = document_hasher()
    for page in pages:
        hasher.update(page.encode())
    return hasher.hexdigest()


def test_the_same_pages_hash_the_same():
    assert hash_pages("first page", "second page") == hash_pages(
        "first page", "second page"
    )


def test_pages_in_a_different_order_hash_differently():
    # order sensitivity is what the streaming hasher buys over collecting per page digests and
    # combining them, and it is why the same pages rearranged are not treated as a duplicate
    assert hash_pages("first page", "second page") != hash_pages(
        "second page", "first page"
    )


def test_changing_one_character_changes_the_hash():
    assert hash_pages("a document") != hash_pages("a documant")


def test_a_page_fed_in_two_parts_hashes_as_one():
    # the ingest path feeds a page at a time, so nothing may depend on where the updates fall
    hasher = document_hasher()
    hasher.update(b"first ")
    hasher.update(b"page")
    assert hasher.hexdigest() == hash_pages("first page")


def test_pages_are_hashed_as_utf8():
    # a stable value rather than just "it doesn't raise": the digest is stored and compared across
    # ingests, so it may not depend on anything about the running interpreter
    assert hash_pages("café") == hash_pages("café")
    assert hash_pages("café") != hash_pages("cafe")


def test_identical_chunks_hash_identically_and_different_ones_do_not():
    # what prompting collapses on: identical text has to collapse together, and nothing else may
    assert chunk_hash("a chunk of text") == chunk_hash("a chunk of text")
    assert chunk_hash("a chunk of text") != chunk_hash("a chunk of texts")


def test_a_document_and_a_binary_of_the_same_bytes_do_not_share_a_hash():
    # the two are stored in different places and one of them is an id, so this is domain
    # separation rather than a correctness requirement - but it is free, and a shared value would
    # be a confusing thing to meet in an index
    binary = binary_hasher()
    binary.update(b"the same bytes")
    assert binary.hexdigest() != hash_pages("the same bytes")
