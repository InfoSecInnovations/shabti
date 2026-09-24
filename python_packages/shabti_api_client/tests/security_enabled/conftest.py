import pytest_asyncio
import os
from shabti_keycloak import get_keycloak_client
from shabti_api_client import ShabtiAuthorizationClient
import secrets
from .lib import get_admin_client

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)
prompt_filename = "test_doc.txt"
prompt_file_path = os.path.join(
    os.path.dirname(__file__), "..", "assets", prompt_filename
)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def shabti_instance():
    # nothing to wait for here: compose only starts this once the API reports healthy, which it
    # only starts once every service it depends on does
    yield
    client = await get_admin_client()
    for collection in await client.get_collections():
        try:
            await client.delete_collection(collection.collection_id)
        except Exception:
            pass


# prompting runs on whichever chat model is currently loaded, so we make sure there is one
@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def loaded_chat_model(shabti_instance):
    client = await get_admin_client()
    models = await client.get_models(tags=["chat"])
    model_id = next(
        (m["id"] for m in models["data"] if "default" in m["tags"]),
        models["data"][0]["id"],
    )
    async for _ in client.load_model(model_id):
        pass
    return model_id


@pytest_asyncio.fixture(scope="function")
async def shabti_user_client(request):
    keycloak_client = get_keycloak_client()
    token = await keycloak_client.a_token(username=request.param, password="test")
    yield ShabtiAuthorizationClient(
        server_url="https://shabti:15131",
        token=token,
        keycloak_client=keycloak_client,
        verify=os.getenv("ROOT_CA"),
    )


@pytest_asyncio.fixture(scope="function")
async def shabti_collection_id(request):
    client = await get_admin_client()
    collection_id = await client.create_collection(
        secrets.token_hex(8), request.param["location"], request.param["username"]
    )
    yield collection_id
    try:
        await client.delete_collection(collection_id)
    except Exception:  # the collection may have already been deleted by a test
        pass


@pytest_asyncio.fixture(scope="function")
async def shabti_document_id(shabti_collection_id):
    client = await get_admin_client()
    document_id = None
    async for info in client.insert_files(shabti_collection_id, [file_path]):
        document_id = info.document_id
    yield document_id


@pytest_asyncio.fixture(scope="function")
async def shabti_prompt_document_id(shabti_collection_id):
    client = await get_admin_client()
    document_id = None
    async for info in client.insert_files(shabti_collection_id, [prompt_file_path]):
        document_id = info.document_id
    yield document_id
