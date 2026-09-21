"""What a failed file says it failed for.

The file route recognises a handful of exception types and flattens everything else into "could not
be loaded". A broken or misconfigured embeddings model landed there, so an installation that could
not embed anything at all reported a batch of unreadable documents instead - the one thing that was
not wrong with them. The URL route already reports these as themselves.
"""

import pytest
from isi_util.stream_pool import StreamPool
from shabti_types import (
    DocumentIngestInfo,
    EmbeddingsConfigError,
    EmbeddingsModelMismatchError,
)

from ...src.app.functionality import insert_uploaded_files as module
from ...src.app.functionality.ingest_events import ItemFailed
from ...src.app.functionality.insert_uploaded_files import insert_uploaded_files
from ...src.app.functionality.save_uploads import SavedUpload

ENTRY = SavedUpload(
    item_id="item-1", filename="a-doc.txt", label="a-doc.txt", binary_hash="hash-1"
)


class Client:
    """A collection that holds nothing, so the duplicate shortcut doesn't fire."""

    async def exists(self, index, id):
        return False


@pytest.fixture
def failing_ingest(monkeypatch):
    """Everything up to the embedding works; `insert` raises whatever is asked for."""

    def configure(error):
        def insert_document(*args, **kwargs):
            async def generate():
                raise error
                yield  # pragma: no cover - never reached, makes this a generator

            return generate()

        monkeypatch.setattr(module, "get_client", Client)
        monkeypatch.setattr(module, "is_plain_archive", lambda _: False)
        monkeypatch.setattr(module, "load_saved", lambda entry: object())
        monkeypatch.setattr(module, "insert_document", insert_document)

        # the binary is deleted on the way out, and there is no files directory here
        async def discard(item_id):
            return None

        monkeypatch.setattr(module, "discard", discard)

    return configure


async def run_one(error):
    """The events one file produces when its ingest raises `error`."""
    async with StreamPool[DocumentIngestInfo](limit=1) as pool:
        return [
            event
            async for event in insert_uploaded_files(
                None, "collection-1", [ENTRY], pool
            )
        ]


async def test_an_unusable_model_says_so_rather_than_blaming_the_file(failing_ingest):
    failing_ingest(
        EmbeddingsConfigError(
            model="embed-1",
            message="embed-1 returned a vector that is not made of numbers.",
        )
    )
    failed = [event for event in await run_one(None) if isinstance(event, ItemFailed)]
    assert len(failed) == 1
    assert failed[0].error.error == "EmbeddingsConfigError"
    assert failed[0].error.message == (
        "embed-1 returned a vector that is not made of numbers."
    )
    assert failed[0].error.label == "a-doc.txt"


async def test_a_model_mismatch_is_reported_as_itself(failing_ingest):
    failing_ingest(
        EmbeddingsModelMismatchError(
            indexed_model="old-1",
            current_model="embed-1",
            message="this collection was indexed with old-1",
        )
    )
    failed = [event for event in await run_one(None) if isinstance(event, ItemFailed)]
    assert failed[0].error.error == "EmbeddingsModelMismatchError"
    assert "old-1" in failed[0].error.message


async def test_an_unrecognised_failure_still_falls_back(failing_ingest):
    # the generic branch is what keeps an unknown failure from ending the batch, and it stays
    failing_ingest(RuntimeError("something else entirely"))
    failed = [event for event in await run_one(None) if isinstance(event, ItemFailed)]
    assert failed[0].error.error == "UnsupportedFileError"
    assert failed[0].error.message == "File a-doc.txt could not be loaded"
