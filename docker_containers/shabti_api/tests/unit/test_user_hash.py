"""Who a remote provider is told a request is for."""

import hashlib
import pytest

from ...src.app.functionality.providers import user_hash as user_hash_module
from ...src.app.functionality.providers.config import Provider
from ...src.app.functionality.providers.user_hash import provider_user

REMOTE = Provider(id="remote", name="Remote", base_url="https://api.test/v1")
LOCAL = Provider(id="local", name="Local models", base_url="http://llm-host:11434/v1")


@pytest.fixture
def salt(monkeypatch):
    """`salt(value)` is the installation's salt. The user is always `a-user`."""

    async def user_id(credentials):
        return "a-user"

    monkeypatch.setattr(user_hash_module, "user_id", user_id)

    def set_salt(value):
        monkeypatch.setattr(user_hash_module, "instance_salt", lambda: value)

    set_salt("a-salt")
    return set_salt


async def test_the_user_is_hashed_with_the_salt(salt):
    expected = hashlib.sha256(b"a-salt" + b"a-user").hexdigest()
    assert await provider_user(REMOTE, "a-token") == expected


async def test_another_installation_hashes_the_same_user_differently(salt):
    first = await provider_user(REMOTE, "a-token")
    salt("another-salt")
    assert await provider_user(REMOTE, "a-token") != first


async def test_nobody_is_sent_with_security_disabled(salt):
    assert await provider_user(REMOTE, None) is None


async def test_nobody_is_sent_without_a_salt(salt):
    # unsalted, the same user could be recognised across installations
    salt(None)
    assert await provider_user(REMOTE, "a-token") is None


async def test_llama_cpp_is_not_told(salt):
    assert await provider_user(LOCAL, "a-token") is None
