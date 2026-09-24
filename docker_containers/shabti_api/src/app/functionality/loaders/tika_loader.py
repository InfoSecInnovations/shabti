from .base_loader import (
    ShabtiDocument,
    ShabtiPageStream,
    get_current_time,
    page_list_stream,
)
from . import tika_client
from lxml import etree


def extract_pages(xhtml: str) -> list[ShabtiDocument.ShabtiPage]:
    """Pull the page divs out of Tika's XHTML.

    lxml's pull parser rather than BeautifulSoup: on a 2000 page PDF `html.parser` spent longer on
    this than the Tika server spent producing the document in the first place, and built a tree
    several times the size. Emitted elements are dropped as we go, which is what keeps the parse flat
    in memory, so that can only happen on the page branch: a document with no page divs at all needs
    the whole tree intact for the fallback below.
    """
    pull_parser = etree.HTMLPullParser(events=("end",), no_network=True, recover=True)
    pages: list[ShabtiDocument.ShabtiPage] = []

    def drain():
        for _, element in pull_parser.read_events():
            if element.tag != "div" or element.get("class") != "page":
                continue
            pages.append(
                ShabtiDocument.ShabtiPage(
                    metadata=ShabtiDocument.ShabtiPage.PageMetadata(
                        page_number=len(pages) + 1
                    ),
                    content="".join(element.itertext()),
                )
            )
            element.clear()
            previous = element.getprevious()
            while previous is not None:
                element.getparent().remove(previous)
                previous = element.getprevious()

    pull_parser.feed(xhtml)
    drain()
    root = pull_parser.close()
    drain()
    if pages:
        return pages

    # formats that aren't paginated (plain text, word processor documents) produce no page divs at
    # all, and become a single page holding the whole body
    text = "".join(root.itertext()) if root is not None else ""
    if not text.strip():
        return []
    return [
        ShabtiDocument.ShabtiPage(
            metadata=ShabtiDocument.ShabtiPage.PageMetadata(), content=text
        )
    ]


def get_languages(metadata) -> list[str]:
    """Every language the container declares.

    Tika reports this as a bare string for most formats and a list where a document declares more
    than one. Deduplicated because a document can declare the same one twice.
    """
    language = metadata.get("dc:language")
    if not language:
        return []
    values = language if isinstance(language, list) else [language]
    # `dict.fromkeys` rather than a set: the first declared language should stay first
    return list(dict.fromkeys(value for value in values if value))


class TikaFileLoader:
    @staticmethod
    def load(file, filename: str | None) -> ShabtiPageStream:
        date_time = get_current_time()
        # an entry per embedded resource as well as one for the container, which comes first.
        # PDF OCR is turned off in the server's config, as it can be very slow
        entries = tika_client.recursive_metadata(file.read())
        container = entries[0] if entries else {}
        return page_list_stream(
            ShabtiDocument.DocumentMetadata(
                source=filename,
                filename=filename,
                ingest_date=date_time,
                # never None: `DocumentIngestInfo.document_type` is a required str, and Tika
                # omits the header for a format it could not identify at all
                media_type=container.get("Content-Type") or "application/octet-stream",
                languages=get_languages(container),
            ),
            # the container's own text only. each embedded resource's is a whole XHTML document
            # of its own, and appending them after the container's `</html>` - which is what
            # tika-python did - only ever looked like it kept them: the parse stops there. no
            # `tk:content` at all means no text, which is an empty document rather than an
            # unreadable one, and the caller reports it as such
            extract_pages(container.get("tk:content") or ""),
        )
