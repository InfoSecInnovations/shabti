"""What each provider is found to offer.

httpx and llama.cpp are stubbed on the module under test, and the models.dev snapshot is replaced
with a small one, so these tests are about how a listing becomes what a client is shown.
"""

import json
import logging
from dataclasses import replace
import httpx
import pytest
from shabti_types import ProviderAuthError, ProviderUnavailableError

from ...src.app.functionality.providers import config as config_module
from ...src.app.functionality.providers import discovery as discovery_module
from ...src.app.functionality.providers import models_dev as models_dev_module
from ...src.app.functionality.providers.config import Provider
from ...src.app.functionality.providers.discovery import (
    FALLBACK_CONTEXT_LENGTH,
    list_all_models,
    list_models,
)
from ...src.app.functionality.providers.models_dev import lookup

REMOTE = Provider(
    id="remote",
    name="Remote",
    base_url="https://api.test/v1",
    headers={"X-Org": "an-org"},
)
LOCAL = Provider(id="local", name="Local models", base_url="http://llm-host:11434/v1")

SNAPSHOT = {
    "providers": {
        "test": {
            "api": "https://api.test/v1",
            "models": {"chat-a": {"limit": {"context": 64000}}},
        },
        "elsewhere": {
            "api": "https://api.elsewhere/v1",
            "models": {
                "chat-a": {"limit": {"context": 32000}},
                "chat-b": {"limit": {"context": 16000}},
            },
        },
        "openai": {"models": {"gpt-x": {"limit": {"context": 400000}}}},
    }
}


class Remote:
    """Stands in for the provider's API: `listing` is what `GET /models` answers with."""

    def __init__(self):
        self.status = 200
        self.listing = {"data": [{"id": "chat-a"}, {"id": "chat-b"}]}
        self.error = None
        self.clients = []
        self.gets = []

    def client(self, **kwargs):
        remote = self
        remote.clients.append(kwargs)

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, headers=None):
                remote.gets.append((url, headers))
                if remote.error:
                    raise remote.error
                return httpx.Response(remote.status, json=remote.listing)

        return Client()


@pytest.fixture
def remote(monkeypatch, tmp_path):
    discovery_module.clear_cache()
    # no providers file, so the cache is only cleared by the tests that write one
    monkeypatch.setattr(config_module, "CONFIG_PATH", str(tmp_path / "providers.json"))
    monkeypatch.setattr(models_dev_module, "snapshot", lambda: SNAPSHOT)
    monkeypatch.setattr(discovery_module, "effective_key", lambda p, c: "sk-1")
    api = Remote()
    monkeypatch.setattr(discovery_module.httpx, "AsyncClient", api.client)
    return api


@pytest.fixture
def local_models(monkeypatch):
    async def get_models(tags=None, reload=False):
        return {
            "data": [
                {
                    "id": "chat-1",
                    "tags": ["chat", "default"],
                    "status": {"value": "loaded"},
                },
                {
                    "id": "embed-1",
                    "tags": ["embeddings"],
                    "status": {"value": "unloaded"},
                },
                {"id": "untagged", "tags": [], "status": {"value": "unloaded"}},
            ]
        }

    monkeypatch.setattr(discovery_module, "get_models", get_models)


async def test_a_listing_is_qualified_with_the_provider(remote):
    models = await list_models(REMOTE, None)
    assert [x.id for x in models] == ["remote/chat-a", "remote/chat-b"]
    assert [x.model_id for x in models] == ["chat-a", "chat-b"]
    assert all(x.provider_id == "remote" and x.status is None for x in models)


async def test_only_the_key_and_the_configured_headers_are_sent(remote):
    await list_models(REMOTE, None)
    assert remote.gets == [
        (
            "https://api.test/v1/models",
            {"X-Org": "an-org", "Authorization": "Bearer sk-1"},
        )
    ]
    # the key would follow a redirect wherever it pointed
    assert remote.clients[0]["follow_redirects"] is False
    assert remote.clients[0]["timeout"]


async def test_no_authorization_is_sent_without_a_key(monkeypatch, remote):
    monkeypatch.setattr(discovery_module, "effective_key", lambda p, c: None)
    await list_models(REMOTE, None)
    assert remote.gets[0][1] == {"X-Org": "an-org"}


async def test_the_allow_list_limits_what_is_offered(remote):
    provider = replace(REMOTE, allow=("chat-b",))
    assert [x.model_id for x in await list_models(provider, None)] == ["chat-b"]


async def test_the_context_length_prefers_the_provider_on_the_same_host(remote):
    # chat-a is known to both, chat-b only to another host, which beats knowing nothing
    lengths = {x.model_id: x.context_length for x in await list_models(REMOTE, None)}
    assert lengths == {"chat-a": 64000, "chat-b": 16000}


async def test_a_context_length_in_the_listing_beats_models_dev(remote):
    remote.listing = {"data": [{"id": "chat-a", "context_length": 128000}]}
    assert (await list_models(REMOTE, None))[0].context_length == 128000


async def test_an_unknown_model_gets_the_fallback(remote):
    remote.listing = {"data": [{"id": "mystery"}]}
    model = (await list_models(REMOTE, None))[0]
    assert model.context_length == FALLBACK_CONTEXT_LENGTH
    assert model.category == "chat"
    assert model.display_name == "mystery"


async def test_an_override_beats_everything(remote):
    provider = replace(
        REMOTE,
        overrides={
            "chat-a": {
                "context_length": 1000,
                "label": "Chat A",
                "category": "embeddings",
            }
        },
    )
    model = (await list_models(provider, None))[0]
    assert (model.context_length, model.display_name, model.category) == (
        1000,
        "Chat A",
        "embeddings",
    )


async def test_an_embeddings_model_is_told_apart_by_its_name(remote):
    remote.listing = {"data": [{"id": "text-embedding-3-small"}, {"id": "chat-a"}]}
    assert [x.model_id for x in await list_models(REMOTE, None, "embeddings")] == [
        "text-embedding-3-small"
    ]
    assert [x.model_id for x in await list_models(REMOTE, None, "chat")] == ["chat-a"]


async def test_a_listing_is_kept_for_a_while(remote):
    await list_models(REMOTE, None)
    await list_models(REMOTE, None)
    assert len(remote.gets) == 1


async def test_a_changed_providers_file_drops_what_was_listed(remote, tmp_path):
    await list_models(REMOTE, None)
    (tmp_path / "providers.json").write_text("{}")
    await list_models(REMOTE, None)
    assert len(remote.gets) == 2


async def test_a_failure_is_not_kept(remote):
    remote.status = 503
    with pytest.raises(ProviderUnavailableError):
        await list_models(REMOTE, None)
    remote.status = 200
    assert await list_models(REMOTE, None)


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, ProviderAuthError),
        (404, ProviderUnavailableError),
        (500, ProviderUnavailableError),
    ],
)
async def test_a_refusal_is_one_of_our_errors(remote, status, expected):
    remote.status = status
    with pytest.raises(expected):
        await list_models(REMOTE, None)


async def test_an_unreachable_provider_is_unavailable(remote):
    remote.error = httpx.ConnectError("no route")
    with pytest.raises(ProviderUnavailableError):
        await list_models(REMOTE, None)


async def test_a_listing_without_data_is_unavailable(remote):
    remote.listing = {"error": "nope"}
    with pytest.raises(ProviderUnavailableError):
        await list_models(REMOTE, None)


async def test_local_models_keep_their_status_and_category(local_models):
    models = await list_models(LOCAL, None)
    assert [(x.id, x.category, x.status) for x in models] == [
        ("local/chat-1", "chat", "loaded"),
        ("local/embed-1", "embeddings", "unloaded"),
    ]


async def test_a_failing_provider_is_left_out_of_the_rest(
    monkeypatch, remote, local_models, caplog
):
    broken = Provider(id="broken", name="Broken", base_url="https://broken.test/v1")

    async def models_of(provider, credentials, category=None):
        if provider.id == "broken":
            raise RuntimeError("Authorization: Bearer sk-secret")
        return await list_models(provider, credentials, category)

    monkeypatch.setattr(discovery_module, "providers", lambda: [LOCAL, broken, REMOTE])
    monkeypatch.setattr(discovery_module, "list_models", models_of)
    with caplog.at_level(logging.WARNING, logger="shabti"):
        models = await list_all_models("chat", None)
    assert [x.id for x in models] == ["local/chat-1", "remote/chat-a", "remote/chat-b"]
    assert "broken" in caplog.text
    # an unexpected error's text could carry what was sent
    assert "sk-secret" not in caplog.text


def test_models_dev_knows_first_party_providers_by_their_host(monkeypatch):
    monkeypatch.setattr(models_dev_module, "snapshot", lambda: SNAPSHOT)
    assert lookup("https://api.openai.com/v1", "gpt-x") == {
        "limit": {"context": 400000}
    }


def test_models_dev_knows_nothing_of_an_unknown_model(monkeypatch):
    monkeypatch.setattr(models_dev_module, "snapshot", lambda: SNAPSHOT)
    assert lookup("https://api.test/v1", "mystery") is None


def test_models_dev_is_read_from_the_configurators_copy(monkeypatch, tmp_path):
    path = tmp_path / "models-dev.json"
    path.write_text(json.dumps(SNAPSHOT))
    monkeypatch.setattr(models_dev_module, "SNAPSHOT_PATH", str(path))
    assert lookup("https://api.test/v1", "chat-a") == {"limit": {"context": 64000}}


@pytest.mark.parametrize("contents", [None, "{not json"])
def test_without_a_usable_copy_models_dev_knows_nothing(
    monkeypatch, tmp_path, contents
):
    # the configurator couldn't fetch one, or it was cut off part way
    path = tmp_path / "models-dev.json"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setattr(models_dev_module, "SNAPSHOT_PATH", str(path))
    assert lookup("https://api.test/v1", "chat-a") is None
