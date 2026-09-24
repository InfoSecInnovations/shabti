import io

from bs4 import BeautifulSoup
from ...src.app.functionality.loaders import tika_client
from ...src.app.functionality.loaders.tika_loader import (
    TikaFileLoader,
    extract_pages,
    get_languages,
)

PAGED = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><meta name="Content-Type" content="application/pdf"/><title>A paged document</title></head>
<body>
<div class="page"><p>First page body.</p><p>Still the first page.</p></div>
<div class="page"><p>Second page body.</p></div>
<div class="page"><p>Third page body.</p></div>
</body></html>"""

UNPAGED = """<html xmlns="http://www.w3.org/1999/xhtml">
<head><meta name="Content-Type" content="text/plain"/><title>A flat document</title></head>
<body><p>Everything is in one block.</p><p>There are no page divs at all.</p></body></html>"""


def old_pages(xhtml: str) -> list[str]:
    """How the loader split a document before it moved to lxml, as the thing to stay equal to."""
    soup = BeautifulSoup(xhtml, "html.parser")
    divs = soup.find_all("div", {"class": "page"})
    return [page.text for page in divs] if divs else [soup.get_text()]


def normalised(text: str) -> str:
    return " ".join(text.split())


def test_paged_document_splits_on_page_divs():
    pages = extract_pages(PAGED)
    assert len(pages) == 3
    assert "Second page body." in pages[1].content


def test_page_numbers_are_assigned_in_order():
    assert [page.metadata.page_number for page in extract_pages(PAGED)] == [1, 2, 3]


def test_a_document_with_no_page_divs_becomes_one_page():
    pages = extract_pages(UNPAGED)
    assert len(pages) == 1
    assert pages[0].metadata.page_number is None
    assert "no page divs at all" in pages[0].content


def test_a_document_with_no_text_produces_no_pages():
    # nothing to embed, so the caller reports it as empty rather than storing a document that can
    # never be retrieved
    assert extract_pages("<html><head></head><body></body></html>") == []


def test_pages_match_what_beautifulsoup_produced():
    # the lxml parse differs from bs4 only in indentation whitespace, which the splitter drops
    for xhtml in (PAGED, UNPAGED):
        assert [normalised(page.content) for page in extract_pages(xhtml)] == [
            normalised(text) for text in old_pages(xhtml)
        ]


def test_clearing_emitted_pages_does_not_lose_later_ones():
    # emitted elements are dropped from the tree to keep the parse flat in memory, and getting that
    # wrong takes the following pages with them
    many = "".join(f'<div class="page"><p>Page {i} text.</p></div>' for i in range(200))
    pages = extract_pages(f"<html><body>{many}</body></html>")
    assert len(pages) == 200
    assert "Page 199 text." in pages[199].content


def test_language_is_read_out_of_the_metadata():
    # this used to store the whole metadata dict
    assert get_languages({"dc:language": "en-GB"}) == ["en-GB"]
    assert get_languages({"dc:language": ["en-GB", "fr"]}) == ["en-GB", "fr"]
    assert get_languages({"Content-Type": "application/pdf"}) == []


def test_a_language_declared_twice_is_listed_once():
    assert get_languages({"dc:language": ["en-GB", "fr", "en-GB"]}) == ["en-GB", "fr"]


BODY = "<html><body><p>Body.</p></body></html>"


def loaded(monkeypatch, *entries):
    """The stream `TikaFileLoader` builds from a given `/rmeta` response, with no Tika involved."""
    monkeypatch.setattr(
        tika_client, "recursive_metadata", lambda *_, **__: list(entries)
    )
    return TikaFileLoader.load(io.BytesIO(b"anything"), "test_docs.zip")


def test_the_container_content_type_is_the_media_type(monkeypatch):
    # a zip has an entry per member, each with a type of its own, and the document is the zip
    stream = loaded(
        monkeypatch,
        {"Content-Type": "application/zip", "tk:content": BODY},
        {"Content-Type": "text/plain", "tk:content": BODY},
        {"Content-Type": "text/markdown", "tk:content": BODY},
    )
    assert stream.metadata.media_type == "application/zip"


def test_languages_come_from_the_container_only(monkeypatch):
    stream = loaded(
        monkeypatch,
        {"Content-Type": "application/zip", "dc:language": "en-GB"},
        {"Content-Type": "text/plain", "dc:language": "fr"},
    )
    assert stream.metadata.languages == ["en-GB"]


def test_a_file_with_no_content_type_still_has_a_media_type(monkeypatch):
    # `DocumentIngestInfo.document_type` is a required str, so None is not an option
    assert loaded(monkeypatch, {}).metadata.media_type == "application/octet-stream"


def test_an_empty_response_still_has_a_media_type(monkeypatch):
    assert loaded(monkeypatch).metadata.media_type == "application/octet-stream"


async def test_the_content_is_the_containers_own(monkeypatch):
    # tika-python appended every entry's XHTML after the container's, and the parse stopped at the
    # container's `</html>`, so this is what was ingested all along
    stream = loaded(
        monkeypatch,
        {"tk:content": "<html><body><p>The container.</p></body></html>"},
        {"tk:content": "<html><body><p>An embedded image.</p></body></html>"},
    )
    text = "".join([page.content async for page in stream.pages])
    assert "The container." in text
    assert "An embedded image." not in text


async def test_a_file_tika_extracted_no_text_from_loads_as_an_empty_document(
    monkeypatch,
):
    # an entry with no text at all has no `tk:content`. That used to be a TypeError in the parse,
    # so an image with OCR off read as unloadable rather than as empty
    stream = loaded(monkeypatch, {"Content-Type": "image/png"})
    assert [page async for page in stream.pages] == []
