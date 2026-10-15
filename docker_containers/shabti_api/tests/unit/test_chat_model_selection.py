"""Which chat model a prompt answers with.

The providers and Keycloak are stubbed on the module under test: the model list is what discovery
returns across every provider, and the stored selection is whatever the user last picked.
"""

from dataclasses import replace
import pytest
from shabti_types import ModelNotFoundError, ProviderKeyMissingError, ProviderModelInfo

from ...src.app.functionality import chat_model_selection as selection_module
from ...src.app.functionality.providers import keys as keys_module
from ...src.app.functionality.chat_model_selection import (
    chat_model_selection,
    resolve_prompt_model,
    set_chat_model_selection,
)
from ...src.app.functionality.providers.config import Provider, ProvidersConfig

REMOTE = Provider(id="remote", name="Remote", base_url="https://api.test/v1")


def local(model_id, status="unloaded"):
    return ProviderModelInfo(
        id=f"local/{model_id}",
        provider_id="local",
        model_id=model_id,
        display_name=model_id,
        category="chat",
        status=status,
    )


def remote(model_id):
    return ProviderModelInfo(
        id=f"remote/{model_id}",
        provider_id="remote",
        model_id=model_id,
        display_name=model_id,
        category="chat",
    )


@pytest.fixture
def models(monkeypatch):
    """Every provider's chat models. Mutable, so a test can set up its own."""
    found = [local("chat-1", "loaded"), local("chat-2"), remote("vendor/chat-3")]
    # llama.cpp's own listing, which is only asked for its default tag
    tagged = {
        "data": [
            {"id": "chat-1", "tags": ["chat"]},
            {"id": "chat-2", "tags": ["chat", "default"]},
        ]
    }

    async def list_all_models(category, credentials):
        assert category == "chat"
        return found

    async def get_models(tags=None, reload=False):
        return tagged

    def get_provider(provider_id):
        return (
            REMOTE
            if provider_id == "remote"
            else Provider(
                id="local", name="Local models", base_url="http://llm-host:11434/v1"
            )
        )

    monkeypatch.setattr(selection_module, "list_all_models", list_all_models)
    monkeypatch.setattr(selection_module, "get_models", get_models)
    monkeypatch.setattr(selection_module, "get_provider", get_provider)
    return found


@pytest.fixture
def default(monkeypatch):
    """`default(model_id)` is the default chat model the admin configured."""

    def configure(model_id):
        monkeypatch.setattr(
            selection_module,
            "providers_config",
            lambda: ProvidersConfig(default_chat_model=model_id),
        )

    configure(None)
    return configure


@pytest.fixture
def stored(monkeypatch):
    """`stored(model_id)` is the selection Keycloak holds for the user. Returns what was saved."""
    saved = []

    def store(model_id):
        async def get_user_chat_model(credentials):
            return model_id

        monkeypatch.setattr(
            selection_module, "get_user_chat_model", get_user_chat_model
        )

    async def set_user_chat_model(credentials, model_id):
        saved.append(model_id)

    monkeypatch.setattr(selection_module, "set_user_chat_model", set_user_chat_model)
    store(None)
    store.saved = saved
    return store


async def test_the_requested_model_is_used(models, default):
    models[1] = local("chat-2", "loaded")
    resolved = await resolve_prompt_model(None, "local/chat-2")
    assert resolved.id == "local/chat-2"
    assert resolved.model_id == "chat-2"
    assert resolved.provider.id == "local"


async def test_a_remote_model_is_used_with_its_provider(models, default):
    # its id is split on the first `/` only, as the provider's own id has one too
    resolved = await resolve_prompt_model(None, "remote/vendor/chat-3")
    assert resolved.provider == REMOTE
    assert resolved.model_id == "vendor/chat-3"


async def test_the_requested_model_wins_over_the_stored_selection(
    models, default, stored
):
    stored("local/chat-2")
    assert (await resolve_prompt_model("a-token", "local/chat-1")).id == "local/chat-1"


async def test_without_a_request_the_stored_selection_is_used(models, default, stored):
    # with security enabled each user has their own, whatever another user has loaded since
    stored("remote/vendor/chat-3")
    assert (await resolve_prompt_model("a-token", None)).id == "remote/vendor/chat-3"


async def test_without_a_request_or_security_the_loaded_model_is_used(models, default):
    assert (await resolve_prompt_model(None, None)).id == "local/chat-1"


async def test_a_stored_selection_no_longer_listed_falls_back(models, default, stored):
    # the provider or the model may have been removed since it was chosen
    stored("gone/chat")
    default("remote/vendor/chat-3")
    assert await chat_model_selection("a-token") == "remote/vendor/chat-3"


async def test_the_configured_default_comes_before_the_local_one(
    models, default, stored
):
    default("remote/vendor/chat-3")
    assert await chat_model_selection("a-token") == "remote/vendor/chat-3"


async def test_a_configured_default_that_is_not_listed_is_skipped(
    models, default, stored
):
    default("remote/not-listed")
    assert await chat_model_selection("a-token") == "local/chat-2"


async def test_the_local_default_tag_comes_next(models, default, stored):
    assert await chat_model_selection("a-token") == "local/chat-2"


async def test_without_local_models_the_first_listed_is_the_default(
    models, default, stored
):
    models[:] = [remote("vendor/chat-3"), remote("vendor/chat-4")]
    assert await chat_model_selection("a-token") == "remote/vendor/chat-3"


@pytest.mark.parametrize("status", ["loading", "sleeping"])
async def test_a_model_on_its_way_up_or_resting_is_accepted(models, default, status):
    # the router answers once it has finished loading or waking
    models[1] = local("chat-2", status)
    assert (await resolve_prompt_model(None, "local/chat-2")).id == "local/chat-2"


async def test_an_unloaded_model_is_rejected(models, default):
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, "local/chat-2")
    assert raised.value.model == "local/chat-2"
    assert raised.value.message == "Model local/chat-2 is not loaded"


async def test_a_stored_selection_that_is_not_loaded_is_rejected(
    models, default, stored
):
    # rather than answering with the model somebody else switched to
    stored("local/chat-2")
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model("a-token", None)
    assert raised.value.message == "Model local/chat-2 is not loaded"


@pytest.mark.parametrize("model_id", ["not-a-model", "chat-1", "remote/not-listed"])
async def test_an_unknown_model_is_rejected(models, default, model_id):
    # including a local model named without its provider
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, model_id)
    assert raised.value.model == model_id
    assert raised.value.message == f"Model {model_id} is not available"


async def test_no_chat_models_at_all_is_rejected(models, default):
    models.clear()
    with pytest.raises(ModelNotFoundError) as raised:
        await resolve_prompt_model(None, None)
    assert raised.value.message == "No chat model is available"


async def test_a_listed_model_is_stored(models, default, stored):
    await set_chat_model_selection("a-token", "remote/vendor/chat-3")
    assert stored.saved == ["remote/vendor/chat-3"]


async def test_an_unlisted_model_is_not_stored(models, default, stored):
    with pytest.raises(ModelNotFoundError):
        await set_chat_model_selection("a-token", "remote/not-listed")
    assert stored.saved == []


@pytest.fixture
def without_own_key(monkeypatch, models):
    """A provider only taking users' own keys, with security fully enabled and no key added."""

    async def get_user_key(credentials, provider_id):
        return None

    monkeypatch.setattr(keys_module, "get_user_key", get_user_key)
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "True")
    monkeypatch.delenv("IS_SECURITY_DEMO", raising=False)
    monkeypatch.setattr(
        selection_module,
        "get_provider",
        lambda provider_id: replace(REMOTE, credential_mode="user"),
    )


async def test_a_model_needing_the_users_own_key_asks_for_it(
    models, default, without_own_key
):
    # discovery leaves the provider out for a user without a key, which isn't the reason to give
    with pytest.raises(ProviderKeyMissingError) as raised:
        await resolve_prompt_model("a-token", "remote/not-listed")
    assert raised.value.message == "Add your API key for Remote"


async def test_without_security_a_model_needing_a_users_key_is_just_unavailable(
    monkeypatch, models, default, without_own_key
):
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "False")
    with pytest.raises(ModelNotFoundError):
        await resolve_prompt_model(None, "remote/not-listed")
