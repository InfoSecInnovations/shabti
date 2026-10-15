"""Users' own provider keys, as they are sealed, stored in Keycloak and read back.

Keycloak's admin client is replaced with one that keeps users in a dict, and the secrets key is
written into a temporary directory, so these are about what reaches the attribute and what is
made of it.
"""

import base64
import json
import os
import pytest

from ...src.app.functionality import user_settings as user_settings_module
from ...src.app.functionality.providers import credentials as credentials_module
from ...src.app.functionality.providers import rewrap as rewrap_module
from ...src.app.functionality.providers import secret_files
from ...src.app.functionality.providers.credentials import (
    UnreadableKeyError,
    decrypt,
    delete_user_key,
    encrypt,
    get_user_key,
    list_user_keys,
    needs_rewrap,
    set_user_key,
)
from ...src.app.functionality.providers.rewrap import rewrap_all
from ...src.app.functionality.user_settings import (
    CHAT_MODEL_ATTRIBUTE,
    PROVIDER_KEYS_ATTRIBUTE,
)

SUB = "user-1"
KEY = "sk-" + "x" * 40


class FakeAdminClient:
    """Users by id, as Keycloak's admin API hands them out and takes them back."""

    def __init__(self):
        self.users = {}
        self.updates = 0
        self.payloads = []

    async def a_get_user(self, user_id):
        return json.loads(json.dumps(self.users[user_id]))

    async def a_update_user(self, user_id, payload):
        self.users[user_id] = {**self.users[user_id], **payload}
        self.updates += 1
        self.payloads.append(payload)

    def get_users(self, query):
        return [{"id": x} for x in self.users]

    def get_user(self, user_id):
        return json.loads(json.dumps(self.users[user_id]))

    def update_user(self, user_id, payload):
        self.users[user_id] = {**self.users[user_id], **payload}
        self.updates += 1
        self.payloads.append(payload)

    def records(self, user_id=SUB):
        return self.users[user_id].get("attributes", {}).get(PROVIDER_KEYS_ATTRIBUTE)


@pytest.fixture
def secrets_key(monkeypatch, tmp_path):
    """`secrets_key(current, *versions)` writes the key file. Starts with version 1 only."""
    monkeypatch.setattr(secret_files, "SECRETS_DIR", str(tmp_path))
    path = tmp_path / "shabti_secrets_key.json"
    writes = 0

    def write(current, *versions):
        nonlocal writes
        keys = {str(v): base64.b64encode(bytes([v]) * 32).decode() for v in versions}
        path.write_text(json.dumps({"current": current, "keys": keys}))
        # a rewrite within the same tick of the clock would otherwise look unchanged
        writes += 1
        os.utime(path, (writes, writes))

    write(1, 1)
    return write


@pytest.fixture
def keycloak(monkeypatch, secrets_key):
    admin = FakeAdminClient()
    admin.users[SUB] = {
        "id": SUB,
        "username": "a-user",
        "firstName": "A",
        "lastName": "User",
        "email": "a@user",
        "attributes": {CHAT_MODEL_ATTRIBUTE: ["remote/chat"]},
    }
    admin.users["user-2"] = {"id": "user-2", "attributes": {}}
    monkeypatch.setattr(
        user_settings_module, "get_keycloak_admin_client", lambda: admin
    )
    monkeypatch.setattr(rewrap_module, "get_keycloak_admin_client", lambda: admin)

    # the token is the user's id, to keep the tests readable
    async def user_id(credentials):
        return credentials

    monkeypatch.setattr(credentials_module, "user_id", user_id)
    credentials_module._cache.clear()
    return admin


def test_a_key_round_trips(secrets_key):
    record = encrypt(SUB, "remote", KEY)
    assert KEY not in record
    assert record.startswith("remote:1:")
    assert decrypt(SUB, record) == KEY


def test_the_same_key_is_sealed_differently_each_time(secrets_key):
    assert encrypt(SUB, "remote", KEY) != encrypt(SUB, "remote", KEY)


def test_a_key_moved_to_another_user_wont_decrypt(secrets_key):
    with pytest.raises(UnreadableKeyError):
        decrypt("user-2", encrypt(SUB, "remote", KEY))


def test_a_key_moved_to_another_provider_wont_decrypt(secrets_key):
    _, *rest = encrypt(SUB, "remote", KEY).split(":")
    with pytest.raises(UnreadableKeyError):
        decrypt(SUB, ":".join(["elsewhere", *rest]))


def test_a_key_relabelled_with_another_version_wont_decrypt(secrets_key):
    secrets_key(2, 1, 2)
    provider_id, _, *rest = encrypt(SUB, "remote", KEY).split(":")
    with pytest.raises(UnreadableKeyError):
        decrypt(SUB, ":".join([provider_id, "1", *rest]))


def test_a_key_sealed_with_a_removed_version_wont_decrypt(secrets_key):
    record = encrypt(SUB, "remote", KEY)
    secrets_key(2, 2)
    with pytest.raises(UnreadableKeyError):
        decrypt(SUB, record)


@pytest.mark.parametrize("record", ["remote", "remote:1:abc", "remote:1:!!:!!"])
def test_a_malformed_record_wont_decrypt(secrets_key, record):
    with pytest.raises(UnreadableKeyError):
        decrypt(SUB, record)


def test_an_older_version_needs_rewrapping(secrets_key):
    record = encrypt(SUB, "remote", KEY)
    assert not needs_rewrap(record)
    secrets_key(2, 1, 2)
    assert needs_rewrap(record)
    # still readable until it is
    assert decrypt(SUB, record) == KEY


async def test_a_key_is_stored_without_losing_the_other_attributes(keycloak):
    await set_user_key(SUB, "elsewhere", "sk-other")
    await set_user_key(SUB, "remote", KEY)
    attributes = keycloak.users[SUB]["attributes"]
    assert attributes[CHAT_MODEL_ATTRIBUTE] == ["remote/chat"]
    assert [x.split(":")[0] for x in attributes[PROVIDER_KEYS_ATTRIBUTE]] == [
        "elsewhere",
        "remote",
    ]
    assert await get_user_key(SUB, "remote") == KEY
    assert await get_user_key(SUB, "elsewhere") == "sk-other"


async def test_storing_a_key_again_replaces_it(keycloak):
    await set_user_key(SUB, "remote", "sk-old")
    await set_user_key(SUB, "remote", KEY)
    assert len(keycloak.records()) == 1
    assert await get_user_key(SUB, "remote") == KEY


async def test_a_long_key_round_trips(keycloak):
    key = "k" * 1000
    await set_user_key(SUB, "remote", key)
    assert await get_user_key(SUB, "remote") == key


async def test_only_the_last_four_characters_are_listed(keycloak):
    await set_user_key(SUB, "remote", KEY)
    await set_user_key(SUB, "elsewhere", "sk-other-1234")
    assert await list_user_keys(SUB) == {"remote": "xxxx", "elsewhere": "1234"}
    assert await list_user_keys("user-2") == {}


async def test_deleting_a_key_leaves_the_others(keycloak):
    await set_user_key(SUB, "remote", KEY)
    await set_user_key(SUB, "elsewhere", "sk-other")
    await delete_user_key(SUB, "remote")
    assert await get_user_key(SUB, "remote") is None
    assert await list_user_keys(SUB) == {"elsewhere": "ther"}
    assert keycloak.users[SUB]["attributes"][CHAT_MODEL_ATTRIBUTE] == ["remote/chat"]


async def test_a_key_is_read_from_keycloak_once_a_minute(keycloak):
    await set_user_key(SUB, "remote", KEY)
    await get_user_key(SUB, "remote")
    keycloak.users[SUB]["attributes"][PROVIDER_KEYS_ATTRIBUTE] = []
    assert await get_user_key(SUB, "remote") == KEY


async def test_storing_a_key_replaces_the_one_remembered(keycloak):
    await set_user_key(SUB, "remote", "sk-old")
    await get_user_key(SUB, "remote")
    await set_user_key(SUB, "remote", KEY)
    assert await get_user_key(SUB, "remote") == KEY
    await delete_user_key(SUB, "remote")
    assert await get_user_key(SUB, "remote") is None


async def test_an_unreadable_key_is_as_good_as_none(keycloak):
    await set_user_key(SUB, "remote", KEY)
    keycloak.users[SUB]["attributes"][PROVIDER_KEYS_ATTRIBUTE] = [
        "remote:1:AAAAAAAAAAAAAAAA:AAAA"
    ]
    assert await get_user_key(SUB, "remote") is None
    assert await list_user_keys(SUB) == {}


async def test_a_key_under_an_older_version_is_rewrapped_as_its_used(
    keycloak, secrets_key
):
    await set_user_key(SUB, "remote", KEY)
    secrets_key(2, 1, 2)
    credentials_module._cache.clear()
    assert await get_user_key(SUB, "remote") == KEY
    assert keycloak.records()[0].startswith("remote:2:")
    assert keycloak.users[SUB]["attributes"][CHAT_MODEL_ATTRIBUTE] == ["remote/chat"]


async def test_every_users_keys_are_rewrapped(keycloak, secrets_key):
    await set_user_key(SUB, "remote", KEY)
    await set_user_key(SUB, "elsewhere", "sk-other")
    await set_user_key("user-2", "remote", "sk-two")
    secrets_key(2, 1, 2)
    updates = keycloak.updates
    rewrap_all()
    assert keycloak.updates == updates + 2
    assert all(x.split(":")[1] == "2" for x in keycloak.records())
    assert keycloak.records("user-2")[0].startswith("remote:2:")
    assert keycloak.users[SUB]["attributes"][CHAT_MODEL_ATTRIBUTE] == ["remote/chat"]
    credentials_module._cache.clear()
    assert await get_user_key(SUB, "elsewhere") == "sk-other"
    assert await get_user_key("user-2", "remote") == "sk-two"
    # the old version can go once nothing needs it
    secrets_key(2, 2)
    credentials_module._cache.clear()
    assert await get_user_key(SUB, "remote") == KEY


async def test_rewrapping_leaves_a_user_whose_keys_are_current(keycloak, secrets_key):
    await set_user_key(SUB, "remote", KEY)
    updates = keycloak.updates
    rewrap_all()
    assert keycloak.updates == updates


async def test_rewrapping_keeps_a_key_it_cant_read(keycloak, secrets_key, capsys):
    keycloak.users[SUB]["attributes"][PROVIDER_KEYS_ATTRIBUTE] = [
        "remote:1:AAAAAAAAAAAAAAAA:AAAA"
    ]
    secrets_key(2, 1, 2)
    rewrap_all()
    assert keycloak.records() == ["remote:1:AAAAAAAAAAAAAAAA:AAAA"]
    assert "1 provider keys could not be decrypted" in capsys.readouterr().out


PROFILE = {
    "username": "a-user",
    "firstName": "A",
    "lastName": "User",
    "email": "a@user",
}


async def test_storing_a_key_sends_the_rest_of_the_user_back(keycloak, secrets_key):
    # Keycloak takes a name or an email left out of an update as cleared, and makes the user fill
    # them in again before they can log in
    await set_user_key(SUB, "remote", KEY)
    await delete_user_key(SUB, "remote")
    await set_user_key(SUB, "remote", KEY)
    secrets_key(2, 1, 2)
    rewrap_all()
    assert len(keycloak.payloads) == 4
    for payload in keycloak.payloads:
        assert PROFILE.items() <= payload.items()
