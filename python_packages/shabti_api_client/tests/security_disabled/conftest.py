from shabti_api_client import ShabtiClient
import pytest_asyncio
import secrets
import os

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


@pytest_asyncio.fixture(loop_scope="session", autouse=True, scope="session")
async def shabti_client():
    # nothing to wait for here: compose only starts this once the API reports healthy, which it
    # only starts once every service it depends on does
    yield ShabtiClient("http://shabti:15131")


# prompting runs on whichever chat model is currently loaded, so we make sure there is one
@pytest_asyncio.fixture(loop_scope="session", autouse=True, scope="session")
async def loaded_chat_model(shabti_client):
    models = await shabti_client.get_models(tags=["chat"])
    model_id = next(
        (m["id"] for m in models["data"] if "default" in m["tags"]),
        models["data"][0]["id"],
    )
    async for _ in shabti_client.load_model(model_id):
        pass
    return model_id


@pytest_asyncio.fixture(scope="function")
async def shabti_collection_id(shabti_client):
    collection_id = await shabti_client.create_collection(secrets.token_hex(8))
    yield collection_id
    # try:
    #     await shabti_client.delete_collection(collection_id)
    # except Exception:  # collection may have been deleted by a test
    #     pass


@pytest_asyncio.fixture(scope="function")
async def shabti_document_id(shabti_client, shabti_collection_id):
    document_id = None
    async for info in shabti_client.insert_files(shabti_collection_id, [file_path]):
        document_id = info.document_id
    yield document_id
