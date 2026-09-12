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
    assert response.json()["model_name"] == loaded_chat_model


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
        assert response.json()["model_name"] == default_model_id(models_data)
    finally:
        async for _ in load_model(loaded_chat_model):
            pass
