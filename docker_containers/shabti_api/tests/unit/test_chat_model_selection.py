"""Which chat model a prompt answers with.

llama.cpp and Keycloak are stubbed on the module under test: the model list is what the router's
`/models` returns, and the stored selection is whatever the user last picked.
"""

import pytest
from shabti_types import ModelNotFoundError

from ...src.app.functionality import chat_model_selection as selection_module
from ...src.app.functionality.chat_model_selection import resolve_prompt_model


def model(model_id, status="unloaded", tags=("chat",)):
    return {"id": model_id, "tags": list(tags), "status": {"value": status}}


@pytest.fixture
def models(monkeypatch):
    """The chat models llama.cpp reports. Mutable, so a test can set up its own."""
    found = {"data": [model("chat-1", "loaded", ("chat", "default")), model("chat-2")]}

    async def get_models(tags=None, reload=False):
        return found

    monkeypatch.setattr(selection_module, "get_models", get_models)
    return found


@pytest.fixture
def stored(monkeypatch):
    """`stored(model_id)` is the selection Keycloak holds for the user."""

    def store(model_id):
        async def get_user_chat_model(credentials):
            return model_id

        monkeypatch.setattr(
            selection_module, "get_user_chat_model", get_user_chat_model
        )

    return store


async def test_the_requested_model_is_used(models):
    models["data"][1] = model("chat-2", "loaded")
    assert await resolve_prompt_model(None, "chat-2") == "chat-2"


async def test_the_requested_model_wins_over_the_stored_selection(models, stored):
    stored("chat-2")
    assert await resolve_prompt_model("a-token", "chat-1") == "chat-1"


async def test_without_a_request_the_stored_selection_is_used(models, stored):
    # with security enabled each user has their own, whatever another user has loaded since
    models["data"][1] = model("chat-2", "loaded")
    stored("chat-2")
    assert await resolve_prompt_model("a-token", None) == "chat-2"


async def test_without_a_request_or_security_the_loaded_model_is_used(models):
    assert await resolve_prompt_model(None, None) == "chat-1"


@pytest.mark.parametrize("status", ["loading", "sleeping"])
async def test_a_model_on_its_way_up_or_resting_is_accepted(models, status):
    # the router answers once it has finished loading or waking
    models["data"][1] = model("chat-2", status)
    assert await resolve_prompt_model(None, "chat-2") == "chat-2"


async def test_an_unloaded_model_is_rejected(models):
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, "chat-2")
    assert raised.value.model == "chat-2"
    assert raised.value.message == "Model chat-2 is not loaded"


async def test_a_stored_selection_that_is_not_loaded_is_rejected(models, stored):
    # rather than answering with the model somebody else switched to
    stored("chat-2")
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model("a-token", None)
    assert raised.value.message == "Model chat-2 is not loaded"


async def test_an_unknown_model_is_rejected(models):
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, "not-a-model")
    assert raised.value.model == "not-a-model"
    assert raised.value.message == "Model not-a-model is not available"


async def test_no_chat_models_at_all_is_rejected(models):
    models["data"] = []
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, None)
    assert raised.value.message == "No chat model is available"
