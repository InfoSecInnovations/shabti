from ...src.app.functionality.opensearch import close_client
from ...src.load_dotenv import load_env
from ...src.app.app import create_app
from fastapi.testclient import TestClient
from ...src.app.functionality.document_collections import (
    create_collection,
    delete_collection,
    get_collections,
)
from ...src.app.functionality.ingesting import insert_document
from ...src.app.functionality.models import get_models, default_model_id, load_model
import secrets
import pytest_asyncio
import os
from ...src.app.functionality.loading import load_file
from uuid import uuid4
import aiofiles

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


@pytest_asyncio.fixture(loop_scope="session", autouse=True, scope="session")
async def shabti_client():
    # nothing to wait for here: compose only starts this once every service it depends on reports
    # healthy
    load_env()
    # entered as a context manager so every request shares one portal, and so the app's lifespan
    # runs: a portal per request would mean an event loop per request, and the OpenSearch client
    # is bound to the loop it was created on
    with TestClient(create_app()) as client:
        yield client
    collections = await get_collections(None)
    for collection in collections:
        try:
            await delete_collection(None, collection.collection_id)
        except Exception:  # we're not trying to test collection getting and deletion here so just do our best to clean up!
            pass
    await close_client()


# prompting runs on whichever chat model is currently loaded, so we make sure there is one
@pytest_asyncio.fixture(loop_scope="session", autouse=True, scope="session")
async def loaded_chat_model(shabti_client):
    model_id = default_model_id(await get_models(tags=["chat"]))
    async for _ in load_model(model_id):
        pass
    return model_id


@pytest_asyncio.fixture(scope="function")
async def shabti_collection_id():
    collection = await create_collection(None, secrets.token_hex(8))
    yield collection.collection_id
    try:
        await delete_collection(None, collection.collection_id)
    except Exception:  # collection may have already been deleted by the test
        pass


@pytest_asyncio.fixture(scope="function")
async def shabti_document_id(shabti_collection_id):
    unique_filename = uuid4().hex
    with open(file_path, "rb") as f:
        doc = load_file(f, filename)
        binary = f.read()
    async with aiofiles.open(
        os.path.join(os.getenv("SHABTI_FILES_DIR"), unique_filename),
        "wb",
    ) as f:
        await f.write(binary)
    document_id = None
    async for ingest_info in insert_document(
        None, shabti_collection_id, doc, unique_filename
    ):
        document_id = ingest_info.document_id

    yield document_id
