"""The routes users add and remove their own provider keys through.

Called directly, with discovery, storage and the audit log stubbed on the module, so these are
about what is checked before a key is stored.
"""

from dataclasses import replace
import pytest
from shabti_types import (
    ProviderAuthError,
    ProviderKeyInfo,
    ProviderKeyInvalidError,
    ProviderUnavailableError,
    UserKeysUnavailableError,
)

from ...src.app import authorization as authorization_module
from ...src.app.authorization import has_access
from ...src.app.functionality.providers.config import Provider
from ...src.app.routers import secure_routes as routes_module

REMOTE = Provider(
    id="remote", name="Remote", base_url="https://api.test/v1", credential_mode="user"
)


@pytest.fixture
def routes(monkeypatch):
    """What the routes did, with security fully enabled and REMOTE taking users' keys."""
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "True")
    monkeypatch.delenv("IS_SECURITY_DEMO", raising=False)
    done = {
        "tried": [],
        "stored": [],
        "deleted": [],
        "logged": [],
        "error": None,
        "access": True,
    }

    async def has_access(token):
        return done["access"]

    provider = {"value": REMOTE}

    async def fetch_models(provider, key):
        done["tried"].append(key)
        if done["error"]:
            raise done["error"]
        return []

    async def set_user_key(credentials, provider_id, key):
        done["stored"].append((provider_id, key))

    async def delete_user_key(credentials, provider_id):
        done["deleted"].append(provider_id)

    async def list_user_keys(credentials):
        return {provider_id: key[-4:] for provider_id, key in done["stored"]}

    async def log_user_action(token, action, message, **kwargs):
        done["logged"].append((action, message, kwargs))

    monkeypatch.setattr(routes_module, "has_access", has_access)
    monkeypatch.setattr(routes_module, "get_provider", lambda _: provider["value"])
    monkeypatch.setattr(routes_module, "fetch_models", fetch_models)
    monkeypatch.setattr(routes_module, "set_user_key", set_user_key)
    monkeypatch.setattr(routes_module, "delete_user_key", delete_user_key)
    monkeypatch.setattr(routes_module, "list_user_keys", list_user_keys)
    monkeypatch.setattr(routes_module, "log_user_action", log_user_action)
    done["provider"] = provider
    return done


def set_key(key):
    return routes_module.set_provider_credential_route(
        "remote", ProviderKeyInfo(key=key), "a-token"
    )


async def test_users_keys_are_refused_in_the_demo(monkeypatch):
    # the demo's accounts are shared by everyone who has the password
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "True")
    monkeypatch.setenv("IS_SECURITY_DEMO", "True")
    with pytest.raises(UserKeysUnavailableError) as raised:
        await routes_module.user_keys_allowed("a-token")
    assert raised.value.status == 403


async def test_users_keys_are_allowed_with_access(routes):
    await routes_module.user_keys_allowed("a-token")


async def test_users_keys_are_refused_without_any_access(routes):
    routes["access"] = False
    with pytest.raises(UserKeysUnavailableError) as raised:
        await routes_module.user_keys_allowed("a-token")
    assert raised.value.status == 403
    assert raised.value.message == "You need access to Shabti to add API keys"


@pytest.fixture
def user(monkeypatch):
    """`user(roles, permissions)` is who the token belongs to. Returns the permission lookups."""
    asked = []

    def configure(roles, permissions):
        async def get_token_info(token):
            return (
                {"resource_access": {"shabti-auth": {"roles": roles}}} if roles else {}
            )

        async def user_permissions(token):
            asked.append(token)
            return permissions

        monkeypatch.setattr(authorization_module, "get_token_info", get_token_info)
        monkeypatch.setattr(authorization_module, "user_permissions", user_permissions)
        return asked

    return configure


async def test_a_role_is_access_before_there_is_a_collection(user):
    # a read-only user has no `read` until a collection they can read exists
    asked = user(["shared_read"], set())
    assert await has_access("a-token")
    assert asked == []


async def test_the_clients_own_role_is_not_access(user):
    user(["uma_protection"], set())
    assert not await has_access("a-token")


async def test_a_collection_shared_directly_is_access(user):
    user([], {"read"})
    assert await has_access("a-token")


async def test_no_role_and_no_permission_is_no_access(user):
    user([], set())
    assert not await has_access("a-token")


async def test_a_key_is_tried_then_stored(routes):
    assert await set_key(" sk-own-1234\n") == {"remote": "1234"}
    assert routes["tried"] == ["sk-own-1234"]
    assert routes["stored"] == [("remote", "sk-own-1234")]


async def test_storing_a_key_is_logged_without_it(routes):
    await set_key("sk-own-1234")
    assert routes["logged"] == [
        ("SET PROVIDER KEY", "Set their API key for remote", {"provider": "remote"})
    ]
    assert "sk-own" not in repr(routes["logged"])


async def test_a_key_the_provider_rejects_is_not_stored(routes):
    routes["error"] = ProviderAuthError("remote")
    with pytest.raises(ProviderKeyInvalidError) as raised:
        await set_key("sk-wrong")
    # not a 401, which the clients take to mean their own token has expired
    assert raised.value.status == 400
    assert routes["stored"] == []


async def test_a_provider_that_cant_be_reached_stores_nothing(routes):
    routes["error"] = ProviderUnavailableError("remote")
    with pytest.raises(ProviderUnavailableError):
        await set_key("sk-own")
    assert routes["stored"] == []


async def test_an_empty_key_is_refused(routes):
    with pytest.raises(ProviderKeyInvalidError):
        await set_key("  ")
    assert routes["tried"] == []


async def test_a_provider_taking_only_the_shared_key_refuses_users_keys(routes):
    routes["provider"]["value"] = replace(REMOTE, credential_mode="shared")
    with pytest.raises(UserKeysUnavailableError) as raised:
        await set_key("sk-own")
    assert raised.value.status == 400
    assert routes["tried"] == []


async def test_a_key_is_deleted_and_logged(routes):
    await routes_module.delete_provider_credential_route("remote", "a-token")
    assert routes["deleted"] == ["remote"]
    assert routes["logged"][-1][0] == "DELETE PROVIDER KEY"
