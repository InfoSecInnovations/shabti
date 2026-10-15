import pytest
from ...src.app.functionality.models import (
    default_model_id,
    get_models,
    load_model,
    model_status,
    unload_models,
)


def statuses(models_data):
    return {x["id"]: model_status(x) for x in models_data["data"]}


async def test_get_models_with_tags(shabti_client):
    response = shabti_client.get("/models", params={"tags": ["chat"]})
    assert response.status_code == 200
    assert all("tags" in x and "chat" in x["tags"] for x in response.json()["data"])


# with security disabled the selection is whichever model happens to be loaded
async def test_get_chat_model_selection(shabti_client, loaded_chat_model):
    response = shabti_client.get("/models/chat/selection")
    assert response.status_code == 200
    assert response.json()["model_name"] == f"local/{loaded_chat_model}"


# we can't size concurrent models against the available hardware yet, so only one chat
# model may be loaded at a time and selecting another one has to swap it out
async def test_loading_a_chat_model_unloads_the_other_ones(
    shabti_client, loaded_chat_model
):
    chat_models = (await get_models(tags=["chat"]))["data"]
    other = next((x for x in chat_models if x["id"] != loaded_chat_model), None)
    if not other:
        pytest.skip("only one chat model is installed")
    async for _ in load_model(other["id"]):
        pass
    chat_statuses = statuses(await get_models(tags=["chat"]))
    assert chat_statuses[other["id"]] == "loaded"
    assert chat_statuses[loaded_chat_model] == "unloaded"


# chat and embeddings are separate categories, so swapping one must leave the other alone
async def test_loading_a_chat_model_keeps_the_embeddings_model(
    shabti_client, loaded_chat_model
):
    embeddings_models = (await get_models(tags=["embeddings"]))["data"]
    # asserted rather than skipped over: the harness writes the model selection itself and will not
    # build one without an embeddings model, so none being installed means the stack is broken, and
    # a skip would report that as the same green summary as a working one
    assert embeddings_models, "no embeddings model is installed"
    embeddings_id = embeddings_models[0]["id"]
    async for _ in load_model(embeddings_id):
        pass
    async for _ in load_model(loaded_chat_model):
        pass
    assert statuses(await get_models())[embeddings_id] == "loaded"


# with nothing loaded there is no selection to report, so we fall back to the default
async def test_get_chat_model_selection_without_a_loaded_chat_model(
    shabti_client, loaded_chat_model
):
    models_data = await get_models(tags=["chat"])
    async for _ in unload_models([x["id"] for x in models_data["data"]]):
        pass
    try:
        response = shabti_client.get("/models/chat/selection")
        assert response.status_code == 200
        assert response.json()["model_name"] == f"local/{default_model_id(models_data)}"
    finally:
        async for _ in load_model(loaded_chat_model):
            pass


# the test llama.cpp is also configured as the remote provider `stub`, see testing/stack.ts
async def test_get_providers(shabti_client):
    response = shabti_client.get("/providers")
    assert response.status_code == 200
    providers = {x["id"]: x for x in response.json()}
    assert providers["local"]["credential_mode"] == "none"
    assert not providers["local"]["has_shared_key"]
    assert providers["stub"]["has_shared_key"]


async def test_get_provider_models(shabti_client, loaded_chat_model):
    response = shabti_client.get("/providers/models", params={"category": "chat"})
    assert response.status_code == 200
    models = {x["id"]: x for x in response.json()}
    local = models[f"local/{loaded_chat_model}"]
    assert local["model_id"] == loaded_chat_model
    assert local["status"] == "loaded"
    remote = models[f"stub/{loaded_chat_model}"]
    assert remote["provider_id"] == "stub"
    assert remote["status"] is None
    assert all(x["category"] == "chat" for x in models.values())
    # it only takes users' own keys, which nobody has with security disabled
    assert not any(x["provider_id"] == "stub-byok" for x in models.values())


async def test_the_allow_list_keeps_the_embeddings_model_from_the_stub(shabti_client):
    embeddings = (await get_models(tags=["embeddings"]))["data"][0]["id"]
    ids = [x["id"] for x in shabti_client.get("/providers/models").json()]
    assert f"local/{embeddings}" in ids
    assert f"stub/{embeddings}" not in ids


async def test_get_providers_status(shabti_client):
    response = shabti_client.get("/status/providers")
    assert response.status_code == 200
    assert response.json() == {
        "local": {"running": True},
        "stub": {"running": True},
        "stub-byok": {"running": True},
    }


# users' own keys need users, so their routes only exist with security enabled
@pytest.mark.parametrize(
    "method, url",
    [
        ("get", "/providers/credentials"),
        ("put", "/providers/stub-byok/credential"),
        ("delete", "/providers/stub-byok/credential"),
    ],
)
async def test_users_keys_need_security(shabti_client, method, url):
    response = shabti_client.request(method, url, json={"key": "sk-test"})
    assert response.status_code == 404
