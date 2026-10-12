"""Which providers there are, and how a model's id says which one it comes from."""

import pytest
from shabti_types import ModelNotFoundError

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


def test_the_shared_key_is_used(keys):
    keys["remote"] = "sk-1"
    assert effective_key(REMOTE, None) == "sk-1"
    assert provider_info(REMOTE).has_shared_key


def test_a_provider_without_a_key_is_asked_without_one(keys):
    # a self-hosted server often takes none
    assert effective_key(REMOTE, None) is None
    assert not provider_info(REMOTE).has_shared_key


def test_llama_cpp_takes_no_key(monkeypatch, keys):
    monkeypatch.setenv("LLM_HOST", "llm-host")
    keys["local"] = "sk-1"
    local = get_provider("local")
    assert effective_key(local, None) is None
    assert provider_info(local).credential_mode == "none"
