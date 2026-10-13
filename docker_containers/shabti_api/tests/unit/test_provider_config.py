"""The providers the configurator recorded, and the secrets that go with them.

Each test writes its own files into a temporary directory, so the paths the module reads are
pointed there rather than at the mounts a container has.
"""

import json
import os
import pytest
from shabti_types import ProviderConfigError

from ...src.app.functionality.providers import config as config_module
from ...src.app.functionality.providers import secret_files
from ...src.app.functionality.providers.config import (
    EmbeddingsConfig,
    Provider,
    providers_config,
)
from ...src.app.functionality.providers.secret_files import (
    instance_salt,
    provider_keys,
)

PROVIDER = {"id": "remote", "name": "Remote", "base_url": "https://api.test/v1/"}


@pytest.fixture
def write_config(monkeypatch, tmp_path):
    """`write_config(value)` writes the providers file, as JSON unless it is already a string."""
    path = tmp_path / "shabti-providers.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", str(path))
    writes = 0

    def write(value):
        nonlocal writes
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        # a rewrite within the same tick of the clock would otherwise look unchanged
        writes += 1
        os.utime(path, (writes, writes))

    return write


@pytest.fixture
def secrets_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(secret_files, "SECRETS_DIR", str(tmp_path))
    return tmp_path


def test_no_file_means_no_providers(write_config):
    assert providers_config().providers == ()
    assert providers_config().default_chat_model is None


def test_a_provider_is_read_with_its_defaults(write_config):
    write_config({"providers": [PROVIDER], "default_chat_model": "remote/chat"})
    config = providers_config()
    assert config.providers == (
        # without the trailing slash, as paths are appended to it
        Provider(id="remote", name="Remote", base_url="https://api.test/v1"),
    )
    assert config.default_chat_model == "remote/chat"


def test_everything_a_provider_can_carry_is_read(write_config):
    write_config(
        {
            "providers": [
                {
                    **PROVIDER,
                    "headers": {"X-Org": "an-org"},
                    "user_id_field": "safety_identifier",
                    "models": {
                        "allow": ["chat"],
                        "overrides": {"chat": {"context_length": 1000}},
                    },
                }
            ]
        }
    )
    provider = providers_config().providers[0]
    assert provider.headers == {"X-Org": "an-org"}
    assert provider.user_id_field == "safety_identifier"
    assert provider.allow == ("chat",)
    assert provider.overrides == {"chat": {"context_length": 1000}}


def test_no_allow_list_offers_everything(write_config):
    write_config({"providers": [PROVIDER]})
    assert providers_config().providers[0].allow is None


def test_an_empty_allow_list_is_kept_apart_from_none(write_config):
    # a provider added only to embed with offers none of its chat models
    write_config({"providers": [{**PROVIDER, "models": {"allow": []}}]})
    assert providers_config().providers[0].allow == ()


def test_a_rewritten_file_is_read_again(write_config):
    # providers can be managed while the API is running
    write_config({"providers": [PROVIDER]})
    assert providers_config().providers[0].name == "Remote"
    write_config({"providers": [{**PROVIDER, "name": "Renamed"}]})
    assert providers_config().providers[0].name == "Renamed"


@pytest.mark.parametrize(
    "provider",
    [
        {**PROVIDER, "id": "Not A Slug"},
        {**PROVIDER, "id": "local"},
        {**PROVIDER, "base_url": "ftp://api.test"},
        {**PROVIDER, "base_url": "api.test/v1"},
        {**PROVIDER, "credential_mode": "per-user"},
        "remote",
    ],
)
def test_an_invalid_provider_is_refused(write_config, provider):
    write_config({"providers": [provider]})
    with pytest.raises(ProviderConfigError) as raised:
        providers_config()
    assert raised.value.status == 500


def test_duplicate_ids_are_refused(write_config):
    write_config({"providers": [PROVIDER, PROVIDER]})
    with pytest.raises(ProviderConfigError):
        providers_config()


def test_a_file_that_is_not_json_is_refused(write_config):
    write_config("{not json")
    with pytest.raises(ProviderConfigError):
        providers_config()


def test_no_embeddings_section_embeds_locally(write_config):
    write_config({"providers": [PROVIDER]})
    assert providers_config().embeddings is None


def test_the_embeddings_model_is_read(write_config):
    write_config(
        {
            "providers": [PROVIDER],
            "embeddings": {"model": "remote/text-embed/v2", "dimensions": 256},
        }
    )
    assert providers_config().embeddings == EmbeddingsConfig(
        model="remote/text-embed/v2", dimensions=256
    )


def test_a_local_embeddings_model_can_be_named(write_config):
    write_config({"embeddings": {"model": "local/all-MiniLM-L6-v2"}})
    assert providers_config().embeddings == EmbeddingsConfig(
        model="local/all-MiniLM-L6-v2"
    )


@pytest.mark.parametrize(
    "embeddings",
    [
        "remote/text-embed",
        {"model": "text-embed"},
        {"model": "elsewhere/text-embed"},
        {"model": "remote/text-embed", "dimensions": 0},
        {"model": "remote/text-embed", "dimensions": "256"},
        {"model": "remote/text-embed", "dimensions": True},
    ],
)
def test_an_invalid_embeddings_section_is_refused(write_config, embeddings):
    write_config({"providers": [PROVIDER], "embeddings": embeddings})
    with pytest.raises(ProviderConfigError):
        providers_config()


def test_no_keys_file_means_no_keys(secrets_dir):
    assert provider_keys() == {}


def test_the_keys_are_read_by_provider(secrets_dir):
    (secrets_dir / "shabti_provider_keys.json").write_text('{"remote": "sk-1"}')
    assert provider_keys() == {"remote": "sk-1"}


def test_an_unreadable_keys_file_does_not_quote_it(secrets_dir):
    (secrets_dir / "shabti_provider_keys.json").write_text('{"remote": sk-secret')
    with pytest.raises(ProviderConfigError) as raised:
        provider_keys()
    assert "sk-secret" not in raised.value.message
    assert raised.value.__cause__ is None


def test_the_salt_is_read_without_its_newline(secrets_dir):
    (secrets_dir / "shabti_instance_salt").write_text("a-salt\n")
    assert instance_salt() == "a-salt"


@pytest.mark.parametrize("contents", [None, "", "\n"])
def test_no_salt_is_none(secrets_dir, contents):
    if contents is not None:
        (secrets_dir / "shabti_instance_salt").write_text(contents)
    assert instance_salt() is None
