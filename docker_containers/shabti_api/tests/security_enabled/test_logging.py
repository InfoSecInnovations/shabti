"""What actually reaches shabti_log.json when the whole stack runs a collection's lifecycle.

The secured mirror of the security_disabled module: same sequence, but every entry should carry the
user it was done on behalf of, and the collection should be logged as an AuthzCollectionInfo.

The rest of the suite runs with logging off: the shared `log_file` fixture turns it on for this
module alone, rather than making every other test write audit entries nothing asserts on.
"""

import json
import os
import secrets
from uuid import uuid4

import aiofiles
import pytest
from shabti_keycloak import get_keycloak_admin_openid_token

from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
    delete_document,
)
from ...src.app.functionality.ingesting import insert_document
from ...src.app.functionality.loading import load_file
from ...src.app.shabti_logging import get_actor

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


async def ingest(token, collection_id):
    unique_filename = uuid4().hex
    with open(file_path, "rb") as f:
        doc = load_file(f, filename)
        binary = f.read()
    async with aiofiles.open(
        os.path.join(os.getenv("SHABTI_FILES_DIR"), unique_filename), "wb"
    ) as f:
        await f.write(binary)
    document_id = None
    async for ingest_info in insert_document(
        await get_actor(token), collection_id, doc, unique_filename
    ):
        document_id = ingest_info.document_id
    return document_id


@pytest.fixture(scope="module")
async def logged_lifecycle(shabti_client, log_file):
    """One collection created, ingested into, emptied and deleted, and the entries it produced.

    The collection is created here rather than through `shabti_collection_id`, which is driven by
    `request.param` and would force an unrelated parametrize onto every test in the module.
    """
    token = get_keycloak_admin_openid_token()["access_token"]
    collection = await create_collection(token, secrets.token_hex(8), "private")
    document_id = await ingest(token, collection.collection_id)
    await delete_document(token, collection.collection_id, document_id)
    await delete_collection(token, collection.collection_id)
    entries = [
        json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
    ]
    return await get_actor(token), collection.collection_id, entries


async def test_every_action_in_the_lifecycle_is_logged(logged_lifecycle):
    _, _, entries = logged_lifecycle
    assert [entry["action"] for entry in entries] == [
        "CREATE COLLECTION",
        "INSERT DOCUMENT",
        "DELETE DOCUMENT",
        "DELETE COLLECTION",
    ]


async def test_a_secured_instance_logs_the_user_behind_every_action(logged_lifecycle):
    actor, _, entries = logged_lifecycle
    for entry in entries:
        assert entry["user"] == {"name": actor.username, "user_id": actor.user_id}


async def test_the_logged_collection_is_an_authz_collection_info(logged_lifecycle):
    actor, collection_id, entries = logged_lifecycle
    for entry in entries:
        assert entry["collection"]["collection_id"] == collection_id
        assert entry["collection"]["location"] == "private"
        assert entry["collection"]["owner"]["user_id"] == actor.user_id


async def test_the_logged_document_carries_its_metadata(logged_lifecycle):
    _, _, entries = logged_lifecycle
    inserted = next(e for e in entries if e["action"] == "INSERT DOCUMENT")
    deleted = next(e for e in entries if e["action"] == "DELETE DOCUMENT")
    assert inserted["document"]["filename"] == filename
    assert inserted["document"]["media_type"]
    assert deleted["document"]["document_id"] == inserted["document"]["document_id"]
    assert deleted["document"]["deleted_element_count"] > 0


async def test_every_entry_has_the_json_envelope(logged_lifecycle):
    _, _, entries = logged_lifecycle
    for entry in entries:
        assert entry["severity"] == "INFO"
        assert entry["log_source"] == "shabti"
        assert entry["eventtime"]
        assert entry["message"]
