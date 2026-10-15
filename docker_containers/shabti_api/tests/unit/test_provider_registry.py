"""Which providers there are, and how a model's id says which one it comes from."""

from dataclasses import replace
import pytest
from shabti_types import ModelNotFoundError, ProviderKeyMissingError

from ...src.app.functionality.providers import keys as keys_module
from ...src.app.functionality.providers import registry as registry_module
from ...src.app.functionality.providers.config import Provider, ProvidersConfig
from ...src.app.functionality.providers.keys import effective_key
from ...src.app.functionality.providers.registry import (
    get_provider,
    provider_info,
    providers,
    split_model_id,
)

REMOTE = Provider(id="remote", name="Remote", base_url="https://api.test/v1")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        registry_module,
        "providers_config",
        lambda: ProvidersConfig(providers=(REMOTE,)),
    )


@pytest.fixture
def keys(monkeypatch):
    found = {}
    monkeypatch.setattr(keys_module, "provider_keys", lambda: found)
    return found


@pytest.fixture
def user_keys(monkeypatch):
    """Each user's own key by provider id, with security fully enabled so they're used."""
    found = {}

    async def get_user_key(credentials, provider_id):
        return found.get(provider_id)

    monkeypatch.setattr(keys_module, "get_user_key", get_user_key)
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "True")
    monkeypatch.delenv("IS_SECURITY_DEMO", raising=False)
    return found


@pytest.mark.parametrize(
    "qualified, expected",
    [
        ("local/chat", ("local", "chat")),
        # only the first `/` is the provider's: remote model ids often have their own
        ("remote/vendor/chat", ("remote", "vendor/chat")),
        ("a/b/c", ("a", "b/c")),
    ],
)
def test_a_model_id_is_split_on_its_first_slash(qualified, expected):
    assert split_model_id(qualified) == expected


@pytest.mark.parametrize("qualified", ["chat", "local/", "/chat", ""])
def test_a_model_id_without_a_provider_is_refused(qualified):
    with pytest.raises(ModelNotFoundError):
        split_model_id(qualified)


def test_llama_cpp_is_the_local_provider_when_installed(monkeypatch, configured):
    monkeypatch.setenv("LLM_HOST", "llm-host")
    assert [x.id for x in providers()] == ["local", "remote"]
    assert get_provider("local").base_url == "http://llm-host:11434/v1"


def test_there_is_no_local_provider_without_llama_cpp(monkeypatch, configured):
    monkeypatch.delenv("LLM_HOST", raising=False)
    assert [x.id for x in providers()] == ["remote"]


def test_an_unknown_provider_is_not_found(monkeypatch, configured):
    with pytest.raises(ModelNotFoundError):
        get_provider("not-a-provider")


async def test_the_shared_key_is_used(keys):
    keys["remote"] = "sk-1"
    assert await effective_key(REMOTE, None) == "sk-1"
    assert provider_info(REMOTE).has_shared_key


async def test_a_provider_without_a_key_is_asked_without_one(keys):
    # a self-hosted server often takes none
    assert await effective_key(REMOTE, None) is None
    assert not provider_info(REMOTE).has_shared_key


async def test_llama_cpp_takes_no_key(monkeypatch, keys):
    monkeypatch.setenv("LLM_HOST", "llm-host")
    keys["local"] = "sk-1"
    local = get_provider("local")
    assert await effective_key(local, None) is None
    assert provider_info(local).credential_mode == "none"


async def test_a_shared_provider_ignores_a_users_key(keys, user_keys):
    keys["remote"] = "sk-shared"
    user_keys["remote"] = "sk-own"
    assert await effective_key(REMOTE, "a-token") == "sk-shared"


@pytest.mark.parametrize("mode", ["user", "shared_or_user"])
async def test_a_users_own_key_comes_first(keys, user_keys, mode):
    keys["remote"] = "sk-shared"
    user_keys["remote"] = "sk-own"
    assert await effective_key(replace(REMOTE, credential_mode=mode), "a-token") == (
        "sk-own"
    )


async def test_without_their_own_key_a_user_falls_back_to_the_shared_one(
    keys, user_keys
):
    keys["remote"] = "sk-shared"
    provider = replace(REMOTE, credential_mode="shared_or_user")
    assert await effective_key(provider, "a-token") == "sk-shared"


async def test_a_provider_taking_only_users_keys_asks_for_one(keys, user_keys):
    # the shared key isn't used even when there is one
    keys["remote"] = "sk-shared"
    provider = replace(REMOTE, credential_mode="user")
    with pytest.raises(ProviderKeyMissingError) as raised:
        await effective_key(provider, "a-token")
    assert raised.value.message == "Add your API key for Remote"
    assert raised.value.status == 400


@pytest.mark.parametrize(
    "security, demo", [("False", None), ("True", "True")], ids=["disabled", "demo"]
)
async def test_users_keys_are_only_used_with_security_fully_enabled(
    monkeypatch, keys, user_keys, security, demo
):
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", security)
    if demo:
        monkeypatch.setenv("IS_SECURITY_DEMO", demo)
    keys["remote"] = "sk-shared"
    user_keys["remote"] = "sk-own"
    provider = replace(REMOTE, credential_mode="shared_or_user")
    assert not keys_module.user_keys_enabled()
    assert await effective_key(provider, "a-token") == "sk-shared"
