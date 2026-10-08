from .md_link import md_link
from .doc_url import doc_url
from datetime import datetime
from shabti_types import PromptSource
import os


def page_link(
    collection_id: str, source: PromptSource, with_target_blank: bool = False
):
    if source.document_metadata.media_type == "application/pdf":
        return f"PDF File: {
            md_link(
                f'{doc_url(collection_id, source.document_metadata.document_id)}#page={source.page_metadata.page_number}',
                f'page {source.page_metadata.page_number} of {source.document_metadata.filename}',
                with_target_blank,
            )
        }"
    if (
        source.document_metadata.media_type == "text/html"
        and not source.document_metadata.filename
    ):
        # we store the timestamp in ms but Python uses s timestamps
        ingest_time = datetime.fromtimestamp(
            source.document_metadata.ingest_date / 1000
        )
        return f"Web page: {md_link(source.page_metadata.source, with_target_blank=with_target_blank)} scraped {ingest_time.ctime()} from parent URL {md_link(source.document_metadata.source, with_target_blank=with_target_blank)}"
    if source.document_metadata.filename:
        extension = os.path.splitext(source.document_metadata.filename)[1]
        return f"{extension} file {
            md_link(
                doc_url(collection_id, source.document_metadata.document_id),
                source.document_metadata.filename,
                with_target_blank,
            )
        }{
            f' (page {source.page_metadata.page_number})'
            if source.page_metadata.page_number
            else ''
        }"
    return f"{source.document_metadata.media_type} type document from {source.document_metadata.source}"


def source_label(source: PromptSource):
    """`page_link` without the links, short enough to name a source in a one-line row"""
    if source.document_metadata.media_type == "application/pdf":
        return f"page {source.page_metadata.page_number} of {source.document_metadata.filename}"
    if (
        source.document_metadata.media_type == "text/html"
        and not source.document_metadata.filename
    ):
        return source.page_metadata.source
    if source.document_metadata.filename:
        return f"{source.document_metadata.filename}{
            f' (page {source.page_metadata.page_number})'
            if source.page_metadata.page_number
            else ''
        }"
    return source.document_metadata.source
