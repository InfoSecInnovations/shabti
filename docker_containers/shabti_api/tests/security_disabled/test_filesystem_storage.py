import aiofiles
import aiofiles.os
import os
from ...src.app.functionality.opensearch import get_document_file_path
from ...src.app.functionality.document_collections import (
    delete_document,
    get_documents,
    delete_collection,
)

filename = "test_doc.txt"
file_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
file_path = os.path.join(file_dir, filename)
archive_name = "test_docs.zip"
archive_members = {"test_doc.txt", "test_doc_2.txt", "prompt_test.md"}


async def test_file_creation(shabti_client, shabti_collection_id, ingest_and_wait):
    with open(file_path, "rb") as f:
        response, ingest, _ = ingest_and_wait(
            "POST",
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
        )
    assert response.status_code == 201
    # the id comes off the ingest's item rather than the response body: the POST is now an
    # acknowledgement, and a document only has an id once its first page has been written
    doc_id = next(item.info.document_id for item in ingest.items if item.info)
    document_file_path = await get_document_file_path(shabti_collection_id, doc_id)
    async with aiofiles.open(
        os.path.join(os.getenv("SHABTI_FILES_DIR"), document_file_path)
    ) as f:
        assert await f.read() == "This is not a real document, it's just a test."


async def test_file_deletion_with_document(shabti_collection_id, shabti_document_id):
    document_file_path = await get_document_file_path(
        shabti_collection_id, shabti_document_id
    )
    await delete_document(None, shabti_collection_id, shabti_document_id)
    assert not await aiofiles.os.path.exists(
        os.path.join(os.getenv("SHABTI_FILES_DIR"), document_file_path)
    )


async def test_file_deletion_with_collection(
    shabti_client, shabti_collection_id, ingest_and_wait, tmp_path
):
    # the archive and one file that is not in it. uploading the rest of the assets alongside it, as
    # this used to, now puts each of them in the collection twice - the archive holds copies of all
    # three - and the second copy of a file a collection already holds is refused. this is still
    # the only test covering an expanded member's binary being deleted with its collection, which
    # is why the archive has to be here and why something has to be uploaded directly beside it
    direct = tmp_path / "not_in_the_archive.txt"
    direct.write_text("A document that the test archive does not also contain.")
    uploads = [os.path.join(file_dir, archive_name), str(direct)]
    for upload in uploads:
        with open(upload, "rb") as f:
            ingest_and_wait(
                "POST",
                f"/collections/{shabti_collection_id}/documents/files",
                files=[("files", f)],
            )
    docs = await get_documents(None, shabti_collection_id)
    paths = [
        await get_document_file_path(shabti_collection_id, doc.document_id)
        for doc in docs.documents
    ]
    # the archive becomes one document per member and none of its own, so there are more documents
    # here than there were files uploaded
    assert len(paths) == len(archive_members) + 1
    assert all(paths)
    await delete_collection(None, shabti_collection_id)
    for path in paths:
        assert not await aiofiles.os.path.exists(
            os.path.join(os.getenv("SHABTI_FILES_DIR"), path)
        )
