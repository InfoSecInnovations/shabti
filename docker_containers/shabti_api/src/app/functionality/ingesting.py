from .opensearch_ingesting import insert
from isi_util.relay import relay
from shabti_types import DocumentIngestInfo, DocumentInfo, UserInfo
from collections.abc import AsyncGenerator
from ..shabti_logging import log_user_action_as, logging_enabled
from .document_collections import get_collection_info
from .opensearch import get_document


def insert_document(
    actor: UserInfo | None, collection_id, stream, binary_path=None
) -> AsyncGenerator[DocumentIngestInfo, None]:
    """Ingest one document, audit logging it once the stream finishes on its own.

    A plain function returning `relay(...)` rather than a generator of its own. `async for x in
    insert(...): yield x` would take the throw a cancellation delivers in *this* frame and leave
    `insert` suspended at its yield, so its rollback would never run and the partial document would
    be orphaned - which is the whole thing cooperative cancellation exists to prevent. `relay`
    forwards the throw into `insert` instead, and its `after` callback is where the log hangs off
    the end of the stream without adding another frame with the same problem.
    """

    async def log(last: DocumentIngestInfo | None) -> None:
        if not logging_enabled() or not last:
            return
        collection_info = await get_collection_info(collection_id)
        doc = await get_document(collection_id, last.document_id)
        document_info = DocumentInfo(
            source=doc["source"],
            ingest_date=doc["ingest_date"],
            vector_count=doc["vector_count"],
            document_id=doc["id"],
            page_count=doc["page_count"],
            media_type=doc["media_type"],
            filename=doc["filename"] if "filename" in doc else None,
            languages=doc["languages"] if "languages" in doc else None,
        )
        message = (
            f"Ingest document with ID {last.document_id} "
            f"into collection with ID {collection_id}"
        )
        log_user_action_as(
            actor,
            "INSERT DOCUMENT",
            message,
            collection=collection_info.model_dump(),
            document=document_info.model_dump(),
        )

    return relay(insert(collection_id, stream, binary_path), after=log)
