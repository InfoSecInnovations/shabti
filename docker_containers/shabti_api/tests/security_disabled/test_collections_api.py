import os
from ...src.app.functionality.document_collections import (
    get_collections,
    get_documents,
)
from shabti_util import auth_enabled
import pytest
import secrets
import json
import asyncio
from uuid import uuid4

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)

zip_filename = "test_docs.zip"
zip_path = os.path.join(os.path.dirname(__file__), "..", "assets", zip_filename)
zip_members = {"test_doc.txt", "test_doc_2.txt", "prompt_test.md"}


def distinct_uploads(tmp_path, count):
    """`count` files holding the same document with a different line in each.

    A collection refuses a second copy of a file it already holds, so a test about ingesting
    several documents at once has to hand it several *different* documents or it stops being a
    test about concurrency and becomes one about the duplicate check.
    """
    paths = []
    for index in range(count):
        path = tmp_path / f"upload_{index}.txt"
        path.write_text(f"This is not a real document, it is just test number {index}.")
        paths.append(path)
    return paths


def test_auth_setting():
    assert not auth_enabled()


async def test_create_collection(shabti_client):
    collection_name = secrets.token_hex(8)
    response = shabti_client.post(
        "/collections", json={"collection_name": collection_name}
    )
    assert response.status_code == 201
    collection_id = response.json()["collection_id"]
    assert collection_id
    # after checking we got the expected type of response, also check the collection actually exists
    collections = await get_collections(None)
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == collection_id
            and collection_info.collection_name == collection_name
        ),
        None,
    )


async def test_list_collections(shabti_client, shabti_collection_id):
    response = shabti_client.get("/collections")
    assert response.status_code == 200
    assert next(
        (
            collection_info
            for collection_info in response.json()
            if collection_info["collection_id"] == shabti_collection_id
        ),
        None,
    )


async def test_insert_documents(shabti_client, shabti_collection_id, ingest_and_wait):
    with open(file_path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    assert ingest.status == "complete"
    docs = await get_documents(None, shabti_collection_id)
    assert next((doc for doc in docs.documents if doc.filename == filename), None)


async def test_a_zip_is_ingested_as_one_document_per_member(
    shabti_client, shabti_collection_id, ingest_and_wait
):
    with open(zip_path, "rb") as f:
        response, ingest, lines = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    # the POST knows only the archive: its members are discovered once the ingest is running
    assert [item["label"] for item in response.json()["items"]] == [zip_filename]
    assert ingest.status == "complete"
    by_label = {item.label: item for item in ingest.items}
    assert set(by_label) == zip_members | {zip_filename}
    archive = by_label[zip_filename]
    # it produced items rather than a document of its own, so it reports how many instead of
    # sitting at "queued" for ever with neither progress nor an error
    assert archive.expanded == 3
    assert archive.info is None and archive.error is None
    # a single item failing does not fail the ingest, so name the item and its error rather than
    # tripping over its missing `info` on the next line
    assert not [item for item in ingest.items if item.error], [
        (item.label, item.error.message) for item in ingest.items if item.error
    ]
    assert all(by_label[label].info.complete for label in zip_members)
    docs = await get_documents(None, shabti_collection_id)
    # one document per member, not one flattened document holding every member's text, and the
    # archive itself was never indexed
    assert {doc.filename for doc in docs.documents} == zip_members
    assert "application/zip" not in {doc.media_type for doc in docs.documents}
    # "ingest" appears in test_doc_2.txt and in neither other member, so finding it through the
    # child text search proves each member was extracted, chunked and embedded on its own
    found = await get_documents(None, shabti_collection_id, search="ingest")
    assert [doc.filename for doc in found.documents] == ["test_doc_2.txt"]
    # the archive stays off the progress stream: it never had a document, and `document_id` is a
    # required field of every line
    assert len([line for line in lines if line.get("complete")]) == 3
    assert not any(line.get("label") == zip_filename for line in lines)


url = "https://www.scrapethissite.com/pages/simple/"
# the listing page above `url`, which unlike `url` itself has children within the crawl scope
listing_url = "https://www.scrapethissite.com/pages/"


async def test_insert_urls(shabti_client, shabti_collection_id, ingest_and_wait):
    response, ingest, _ = ingest_and_wait(
        "POST", f"/collections/{shabti_collection_id}/documents/urls", json=[url]
    )
    assert response.status_code == 201
    assert ingest.status == "complete"
    docs = await get_documents(None, shabti_collection_id)
    doc = next((doc for doc in docs.documents if doc.source == url), None)
    assert doc
    # the default is the page that was asked for, not the whole site
    assert doc.page_count == 1
    assert doc.vector_count > 1
    # the page is a list of 250 countries, and "Zimbabwe" is the last of them. the document search
    # matches child text, so this proves the whole listing survived extraction and chunking rather
    # than being cut down to the handful of rows main content extraction used to keep
    found = await get_documents(None, shabti_collection_id, search="Zimbabwe")
    assert found.documents


async def test_insert_urls_with_depth(
    shabti_client, shabti_collection_id, ingest_and_wait
):
    response, ingest, lines = ingest_and_wait(
        "POST",
        f"/collections/{shabti_collection_id}/documents/urls",
        json=[listing_url],
        params={"max_depth": 2},
    )
    assert response.status_code == 201
    assert ingest.status == "complete"
    # a crawl's estimated total moves as it discovers pages, so `progress + 1 == total` is not a
    # completion signal and `complete` has to be one: this asserts the stream really does contain
    # an event where the two disagree, and still ends with exactly one completion
    assert any(
        not line.get("complete") and line["progress"] + 1 != line["total"]
        for line in lines
    )
    assert len([line for line in lines if line.get("complete")]) == 1
    docs = await get_documents(None, shabti_collection_id)
    doc = next((doc for doc in docs.documents if doc.source == listing_url), None)
    assert doc
    # the listing plus the five pages it links to, all under /pages/
    assert doc.page_count >= 5
    # "Zimbabwe" appears only on the /pages/simple/ child and not on the listing itself, so finding
    # it proves a linked page was actually crawled rather than just the seed
    assert (
        await get_documents(None, shabti_collection_id, search="Zimbabwe")
    ).documents
    # "internet" appears only on the site root, which is above the crawl scope. finding it would
    # mean the crawl had widened past the URL it was given
    assert not (
        await get_documents(None, shabti_collection_id, search="internet")
    ).documents


async def test_document_counts_are_per_document(
    shabti_client, shabti_collection_id, ingest_and_wait
):
    with open(file_path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    assert ingest.status == "complete"
    response, ingest, _ = ingest_and_wait(
        "POST",
        f"/collections/{shabti_collection_id}/documents/urls",
        json=[listing_url],
        params={"max_depth": 2},
    )
    assert response.status_code == 201
    assert ingest.status == "complete"
    docs = await get_documents(None, shabti_collection_id)
    file_doc = next((doc for doc in docs.documents if doc.filename == filename), None)
    crawled_doc = next(
        (doc for doc in docs.documents if doc.source == listing_url), None
    )
    assert file_doc and crawled_doc
    # the counts for a listing come from one aggregation over every document on the page, so each
    # document has to get its own bucket rather than the whole page sharing a count
    assert file_doc.page_count == 1
    assert crawled_doc.page_count >= 5
    assert file_doc.vector_count > 0
    assert crawled_doc.vector_count > 0


@pytest.mark.parametrize(
    "forbidden_url",
    [
        "http://127.0.0.1:9200/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_insert_urls_rejects_internal_addresses(
    shabti_client, shabti_collection_id, forbidden_url
):
    response = shabti_client.post(
        f"/collections/{shabti_collection_id}/documents/urls", json=[forbidden_url]
    )
    assert response.status_code == 403
    assert response.json()["error_type"] == "ForbiddenUrlError"
    docs = await get_documents(None, shabti_collection_id)
    assert not docs.documents


async def test_delete_document(shabti_client, shabti_collection_id, shabti_document_id):
    docs = await get_documents(None, shabti_collection_id)
    # test the document is actually there before deleting it
    assert next(
        (doc for doc in docs.documents if doc.document_id == shabti_document_id), None
    )
    response = shabti_client.delete(
        f"/collections/{shabti_collection_id}/documents/{shabti_document_id}"
    )
    assert response.status_code == 200
    docs = await get_documents(None, shabti_collection_id)
    assert not any(doc.document_id == shabti_document_id for doc in docs.documents)


async def test_delete_collection(shabti_client, shabti_collection_id):
    response = shabti_client.delete(f"/collections/{shabti_collection_id}")
    assert response.status_code == 200
    collections = await get_collections(None)
    response_json = response.json()
    assert not any(
        collection.collection_id == response_json["collection_id"]
        for collection in collections
    )


async def test_documents_ingest_in_parallel(
    shabti_client, shabti_collection_id, ingest_and_wait, tmp_path
):
    handles = [open(path, "rb") for path in distinct_uploads(tmp_path, 3)]
    try:
        response, ingest, lines = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", handle) for handle in handles],
        )
    finally:
        for handle in handles:
            handle.close()
    assert response.status_code == 201
    # every submitted file is reported as an item straight away, before any of them has progress
    assert len(response.json()["items"]) == 3
    assert not any("info" in item for item in response.json()["items"])
    assert ingest.status == "complete"
    ids = {item.info.document_id for item in ingest.items if item.info}
    assert len(ids) == 3
    # exactly one completion per document, and it is the last thing said about it
    completions = [line for line in lines if line.get("complete")]
    assert {line["document_id"] for line in completions} == ids
    assert len(completions) == 3


async def test_ingest_survives_a_detached_client(shabti_client, shabti_collection_id):
    with open(file_path, "rb") as f:
        response = shabti_client.post(
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    ingest_id = response.json()["ingest_id"]
    # nothing read the stream at all, which used to abort the ingest
    while True:
        listed = shabti_client.get("/ingests").json()
        info = next(item for item in listed if item["ingest_id"] == ingest_id)
        if info["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.5)
    assert info["status"] == "complete"
    docs = await get_documents(None, shabti_collection_id)
    assert next((doc for doc in docs.documents if doc.filename == filename), None)


async def test_ingests_beyond_the_cap_queue_rather_than_fail(
    shabti_client, shabti_collection_id, monkeypatch, tmp_path
):
    monkeypatch.setenv("SHABTI_INGEST_MAX_ACTIVE_PER_OWNER", "1")
    ingest_ids = []
    for path in distinct_uploads(tmp_path, 3):
        with open(path, "rb") as f:
            response = shabti_client.post(
                f"/collections/{shabti_collection_id}/documents/files",
                files=[("files", f)],
            )
        # nothing is refused over the cap any more, it waits for a slot
        assert response.status_code == 201
        ingest_ids.append(response.json()["ingest_id"])
    while True:
        listed = {
            item["ingest_id"]: item for item in shabti_client.get("/ingests").json()
        }
        mine = [listed[ingest_id] for ingest_id in ingest_ids]
        if not any(item["status"] in ("queued", "running") for item in mine):
            break
        await asyncio.sleep(0.5)
    assert [item["status"] for item in mine] == ["complete"] * 3
    docs = await get_documents(None, shabti_collection_id)
    assert len(docs.documents) == 3


async def test_a_finished_ingest_stays_readable(
    shabti_client, shabti_collection_id, ingest_and_wait
):
    with open(file_path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert ingest.status == "complete"
    # asked for again after it is over: a terminal snapshot and a closed stream, not a 404
    again = shabti_client.get(f"/ingests/{ingest.ingest_id}")
    assert again.status_code == 200
    lines = [json.loads(line) for line in again.text.splitlines() if line.strip()]
    assert lines and all(line.get("complete") for line in lines)


async def test_ingest_of_a_missing_collection_is_rejected(shabti_client):
    with open(file_path, "rb") as f:
        response = shabti_client.post(
            f"/collections/{uuid4().hex}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 404
    assert response.json()["error_type"] == "CollectionNotFoundError"


async def test_cancelling_an_ingest_leaves_nothing_behind(
    shabti_client, shabti_collection_id
):
    handles = [open(file_path, "rb") for _ in range(3)]
    try:
        response = shabti_client.post(
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", handle) for handle in handles],
        )
    finally:
        for handle in handles:
            handle.close()
    assert response.status_code == 201
    seeded = response.json()
    cancelled = shabti_client.delete(f"/ingests/{seeded['ingest_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    # anything a stopped job had already written is rolled back or swept, and every saved binary
    # goes with it since no document ever claimed one
    for item in seeded["items"]:
        assert not os.path.exists(
            os.path.join(os.getenv("SHABTI_FILES_DIR"), item["item_id"])
        )
