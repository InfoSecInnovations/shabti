"""How a document and a chunk are reduced to something comparable.

Three hashes, answering three different questions, and they are here together so that none of them
gets reinvented somewhere else with a different digest size or a different encoding.

`chunk_hash` identifies one chunk of text. Identical text embeds to an identical vector, so
grouping hits by this is grouping them by vector - which is what prompting collapses on, and
`knn_vector` has no doc values of its own to collapse on.

`document_hasher` identifies a whole document by its extracted text. It hashes the page content
the loader produced rather than the chunks that come out of the splitter: chunk boundaries are
decided by the pinned tokenizer, so hashing those would silently make every stored hash
incomparable the day the embeddings model changes. It is also fed page by page as an ingest
streams, so a document of any size costs one hasher rather than a list of digests.

`binary_hasher` identifies an uploaded file by its bytes, and is fed by the loop that was already
writing those bytes to disk. Its value becomes the document's OpenSearch id, so a second upload of
the same file is refused by the database rather than by anything here.
"""

from hashlib import blake2b

DIGEST_SIZE = 16
# blake2's own domain separation, doubling as a version tag: if what is fed to either hasher ever
# changes, change it too rather than quietly producing values that look like the old ones
DOCUMENT_PERSON = b"shabti-doc-1"
BINARY_PERSON = b"shabti-bin-1"


def chunk_hash(chunk: str) -> str:
    return blake2b(chunk.encode(), digest_size=DIGEST_SIZE).hexdigest()


def document_hasher():
    """A hasher to feed each page's content to, in the order the loader produced them.

    Order sensitive on purpose: the same pages in a different order are a different document, and
    nothing here needs to be able to recompute the value from what was indexed.
    """
    return blake2b(digest_size=DIGEST_SIZE, person=DOCUMENT_PERSON)


def binary_hasher():
    """A hasher to feed a file's bytes to, in the order they are read."""
    return blake2b(digest_size=DIGEST_SIZE, person=BINARY_PERSON)
