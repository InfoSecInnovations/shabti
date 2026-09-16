import asyncio
from contextlib import aclosing, suppress
from opensearchpy.exceptions import ConflictError
from opensearchpy.helpers import async_bulk
from .content_hash import chunk_hash, document_hasher
from .embeddings import (
    context_limit,
    count_tokens,
    create_embeddings,
    get_embeddings_model_id,
)
from .embeddings_config import chunk_size
from .loaders.base_loader import ShabtiDocument, ShabtiPageStream
from .opensearch import (
    get_client,
    check_embeddings_model,
    delete_opensearch_document,
    find_duplicate_document,
)
from shabti_types import (
    DocumentIngestInfo,
    DuplicateDocumentError,
    EmbeddingsConfigError,
    EmptyDocumentError,
)
from semantic_text_splitter import TextSplitter


def get_field_type(python_type):
    if python_type == "int":
        return "long"
    if python_type == "float":
        return "float"
    if python_type == "bool":
        return "boolean"
    return "keyword"


# how much of the previous chunk each chunk repeats, so a sentence split across a boundary is still
# retrievable whole from one side of it
CHUNK_OVERLAP = 50


def get_splitter(model_id: str) -> TextSplitter:
    """A splitter that measures text with the embeddings model's own tokenizer.

    The count comes from llama.cpp rather than from a tokenizer downloaded here, which is what lets
    the embeddings model be swapped: the GGUF repositories the models are loaded from carry no
    token config, so there is nothing to download that is guaranteed to match the running model.

    The splitter asks for a size several times per chunk, so the counts are memoised for the
    document being split and the connection underneath is held open. Built per document rather than
    cached, so the memo is dropped with it instead of growing for the life of the process.
    """
    capacity = chunk_size(model_id)
    limit = context_limit(model_id)
    if limit and capacity > limit:
        # caught here rather than left to llama.cpp, which would refuse the first oversized chunk
        # partway through an ingest with an error about batch sizes that says nothing about which
        # setting is wrong
        raise EmbeddingsConfigError(
            model=model_id,
            message=(
                f"chunk_size for {model_id} is {capacity} tokens, but the model was only trained "
                f"on {limit}. Chunks longer than that cannot be embedded at all."
            ),
        )
    if CHUNK_OVERLAP >= capacity:
        raise ValueError(
            f"chunk_size for {model_id} is {capacity}, which is not more than the "
            f"{CHUNK_OVERLAP} token overlap between chunks"
        )
    sizes: dict[str, int] = {}

    def size(text: str) -> int:
        if text not in sizes:
            sizes[text] = count_tokens(text, model_id)
        return sizes[text]

    return TextSplitter.from_callback(size, capacity, overlap=CHUNK_OVERLAP)


async def insert(
    collection_id: str,
    stream: ShabtiPageStream,
    binary_path: str | None = None,
    binary_hash: str | None = None,
):
    """Embed and index a document's pages as the loader produces them.

    OpenSearch is awaited directly; the splitter and the embeddings server are blocking, so they
    run in threads and the event loop stays free for the loader: a crawl keeps fetching while the
    page before it is being embedded. Within a document each page's write is left running as a
    task, which overlaps the two servers involved: a page is embedded while the previous one's
    vectors are still being written to OpenSearch.
    """
    client = get_client()
    # per-document rather than per-page: the model listing is a round trip and the model doesn't
    # change while one document is being ingested
    model_id = await asyncio.to_thread(get_embeddings_model_id)
    await check_embeddings_model(collection_id, model_id)
    splitter = get_splitter(model_id)

    label = stream.metadata.filename or stream.metadata.source
    # fed page by page as they stream, so identifying the document costs one hasher rather than a
    # second copy of its text
    hasher = document_hasher()
    doc_id: str | None = None
    pending: asyncio.Task | None = None
    # the page whose write is in flight, which is also the next one to report progress for
    written = -1

    async def create_parent() -> str:
        additional = {"binary_path": binary_path} if binary_path else {}
        # the uploaded bytes as the id, with op_type=create, so that a second copy of the same file
        # is refused by OpenSearch itself: atomic, with no read before the write, so two uploads
        # arriving together cannot both get in. a crawl has no bytes and keeps a generated id,
        # which is what the content hash below covers instead
        identity = {"id": binary_hash, "op_type": "create"} if binary_hash else {}
        try:
            return (
                await client.index(
                    index=collection_id,
                    body={
                        "type": "document",
                        "child_item_to_document": "document",
                        # cleared once this document is finished and has been kept: until then
                        # it is out of every listing and out of retrieval, so a half written
                        # document is never shown and never quoted
                        "ingesting": True,
                        **vars(stream.metadata),
                        **additional,
                    },
                    **identity,
                )
            )["_id"]
        except ConflictError as e:
            raise DuplicateDocumentError(
                source=stream.metadata.source,
                existing_document_id=binary_hash,
                message=(
                    f"{label} is already in this collection as document {binary_hash}"
                ),
            ) from e

    def split(page: ShabtiDocument.ShabtiPage) -> list[str]:
        # don't allow empty or whitespace chunks
        return [chunk for chunk in splitter.chunks(page.content) if chunk.strip()]

    async def write(page: ShabtiDocument.ShabtiPage, chunks: list[str], vects) -> None:
        page_id = (
            await client.index(
                index=collection_id,
                body={
                    "child_item_to_document": {"name": "child_item", "parent": doc_id},
                    "type": "page",
                    **vars(page.metadata),
                },
                routing=doc_id,
            )
        )["_id"]
        # flush per page rather than accumulating every vector for every page: a crawl or a large
        # text file can be thousands of vectors. the rollback below deletes children
        # by parent, so already flushed vectors are still cleaned up on failure.
        await async_bulk(
            client,
            [
                {
                    "_index": collection_id,
                    "_routing": doc_id,
                    "child_item_to_document": {
                        "name": "child_item",
                        "parent": doc_id,
                    },
                    "type": "vector",
                    "text": chunk,
                    # what prompting collapses on, so that a chunk which appears in the index more
                    # than once cannot take more than one slot in the reference window
                    "text_hash": chunk_hash(chunk),
                    "document_vector": vect,
                    "page_id": page_id,
                    "doc_id": doc_id,
                }
                for chunk, vect in zip(chunks, vects)
            ],
        )

    def progress(complete: bool = False) -> DocumentIngestInfo:
        return DocumentIngestInfo(
            progress=written,
            total=stream.estimated_total(),
            document_id=doc_id,
            document_type=stream.metadata.media_type,
            label=label,
            # built conditionally rather than passed as False: the routes serialise with
            # `response_model_exclude_unset`, so leaving it unset is what keeps the key off the
            # progress lines existing clients already parse
            **({"complete": True} if complete else {}),
        )

    try:
        # closed explicitly rather than left to the garbage collector: a crawl stream shuts its
        # crawler down from there, and it has to happen when this loop ends however it ends
        async with aclosing(stream.pages) as source:
            async for page in source:
                hasher.update(page.content.encode())
                if doc_id is None:
                    # created on the first page rather than up front, so a source that turns out to
                    # be empty leaves nothing behind to clean up
                    doc_id = await create_parent()
                chunks = await asyncio.to_thread(split, page)
                vects = await asyncio.to_thread(create_embeddings, chunks, model_id)
                if pending:
                    await pending
                    yield progress()
                written += 1
                pending = asyncio.create_task(write(page, chunks, vects))

        if pending:
            await pending
            yield progress()

        if doc_id is None:
            # with a message, like the crawl loader's: `ShabtiError` never calls
            # `Exception.__init__`, so leaving it off is what put an empty string on the ingest
            # item rather than a reason
            raise EmptyDocumentError(
                source=stream.metadata.source,
                message=f"No content could be read from {label}",
            )

        # the parent was created on the first page, before any of the content had been seen, so
        # the hash of it can only land as an update
        content_hash = hasher.hexdigest()
        await client.update(
            index=collection_id,
            id=doc_id,
            body={"doc": {"content_hash": content_hash}},
        )

        # nothing above refreshes, so this is what makes the document searchable and what the
        # vector and page counts read straight after an ingest depend on
        await client.indices.refresh(index=collection_id)

        # after the refresh, so that a document which finished alongside this one is visible to the
        # comparison rather than invisible to it. this catches what the id above cannot: a crawl,
        # and the same content arriving as a different file
        duplicate_of = await find_duplicate_document(
            collection_id, doc_id, content_hash, stream.metadata.ingest_date
        )
        if duplicate_of:
            raise DuplicateDocumentError(
                source=stream.metadata.source,
                existing_document_id=duplicate_of,
                message=(
                    f"{label} is already in this collection as document {duplicate_of}"
                ),
            )

        # after the duplicate check rather than in the update above: a document about to withdraw
        # must never be visible as a finished one, however briefly. refreshed because `complete`
        # below is what tells a client the document can be found
        await client.update(
            index=collection_id,
            id=doc_id,
            body={"doc": {"ingesting": False}},
            refresh=True,
        )

        # after the refresh, not before: a consumer acting on `complete` has to be able to find the
        # document. this is also the last suspension point, so a stop delivered here still lands
        # inside the `try` and rolls the document back, which is right - the ingest was cancelled
        yield progress(complete=True)

    # not BaseException: this cannot catch GeneratorExit, so *closing* this generator rather than
    # throwing into it would skip the rollback and orphan a partial document. that is why the stop
    # in isi_util.stream_pool is an ordinary Exception delivered with athrow, never aclose
    except Exception as e:
        if pending:
            # awaited rather than cancelled: cancelling part way through the bulk would let the
            # rest of it land after the rollback had already run, leaving orphaned vectors behind
            with suppress(Exception):
                await pending
        if doc_id is not None:
            # suppressed so that a rollback failing cannot replace the reason the ingest failed:
            # the caller reports the errors it recognises by type, and anything else reads as
            # "could not be loaded", which tells nobody anything. what is left behind is still
            # flagged `ingesting`, so it stays out of every listing and out of retrieval either
            # way, and the sweep at the next start removes it
            with suppress(Exception):
                await delete_opensearch_document(collection_id, doc_id)
        raise e
