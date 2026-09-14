"""The audit log: what goes into an entry, and what comes out the other end as JSON.

No OpenSearch, Tika or Keycloak. `logging_enabled()` and `auth_enabled()` both read their env var
at call time, so `monkeypatch.setenv` is enough to drive every mode, and the one test that cares
about the file on disk borrows the shared `log_file` fixture, which installs the real formatter
and handler against a temporary directory.
"""

import json
import logging
import os

import pytest
from shabti_types import AuthzCollectionInfo, CollectionInfo, UserInfo

from ...logging_config import logging_config
from ...src.app.functionality import document_collections
from ...src.app import shabti_logging
from ...src.app.shabti_logging import (
    get_actor,
    log_user_action,
    log_user_action_as,
)

actor = UserInfo(username="Ada Lovelace", user_id="user-1")
collection_id = "collection-1"


@pytest.fixture
def logging_on(monkeypatch):
    monkeypatch.setenv("SHABTI_LOGGING_ENABLED", "True")


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "True")


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", "False")


def one_entry(caplog):
    """The single audit entry, ignoring any tripwire warning that went with it."""
    records = [
        record
        for record in caplog.records
        if record.name == "shabti" and record.levelno == logging.INFO
    ]
    assert len(records) == 1
    return records[0]


def token_returning(claims):
    """A stand in for the Keycloak decode, which is the only thing a token is ever used for here."""

    async def get_token_info(token):
        return claims

    return get_token_info


# `get_actor`


async def test_a_request_with_no_token_has_no_actor():
    assert await get_actor(None) is None


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"sub": "user-1", "name": "Ada Lovelace"}, "Ada Lovelace"),
        # a service account token has no `name`, so it falls through rather than failing the
        # request that carries it
        ({"sub": "user-1", "preferred_username": "ada"}, "ada"),
        ({"sub": "user-1"}, "user-1"),
    ],
)
async def test_the_actor_username_falls_back_through_the_claims(
    monkeypatch, claims, expected
):
    monkeypatch.setattr(shabti_logging, "get_token_info", token_returning(claims))
    resolved = await get_actor("a-token")
    assert resolved == UserInfo(username=expected, user_id="user-1")


# what an entry contains


def test_an_entry_carries_its_action_message_user_and_extras(caplog, logging_on):
    caplog.set_level(logging.INFO, logger="shabti")
    log_user_action_as(
        actor,
        "CREATE COLLECTION",
        "Create shared collection basalt",
        collection=CollectionInfo(
            collection_name="basalt", collection_id=collection_id
        ).model_dump(),
    )
    record = one_entry(caplog)
    assert record.getMessage() == "Create shared collection basalt"
    assert record.action == "CREATE COLLECTION"
    assert record.user == {"name": "Ada Lovelace", "user_id": "user-1"}
    assert record.collection["collection_id"] == collection_id


async def test_an_action_logged_from_a_token_carries_the_user(
    monkeypatch, caplog, logging_on, auth_on
):
    caplog.set_level(logging.INFO, logger="shabti")
    monkeypatch.setattr(
        shabti_logging,
        "get_token_info",
        token_returning({"sub": "user-1", "name": "Ada Lovelace"}),
    )
    await log_user_action("a-token", "QUERY", "User ran a prompt")
    assert one_entry(caplog).user == {"name": "Ada Lovelace", "user_id": "user-1"}


async def test_an_unsecured_action_is_logged_without_a_user(
    caplog, logging_on, auth_off
):
    caplog.set_level(logging.INFO, logger="shabti")
    # an unsecured instance passes None all the way down from the route, and the entry goes in
    # attributed to nobody rather than not at all
    await log_user_action(None, "QUERY", "User ran a prompt")
    record = one_entry(caplog)
    assert record.action == "QUERY"
    assert not hasattr(record, "user")


def test_an_unattributed_entry_on_a_secured_instance_warns(caplog, logging_on, auth_on):
    caplog.set_level(logging.INFO, logger="shabti")
    log_user_action_as(None, "QUERY", "User ran a prompt")
    # the entry still goes in - losing the action as well as the attribution would be worse - but
    # it doesn't pass silently
    assert one_entry(caplog).action == "QUERY"
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "no actor on a secured instance" in warnings[0].getMessage()


# logging disabled


@pytest.mark.parametrize("auth", ["True", "False"])
async def test_nothing_is_logged_when_logging_is_disabled(monkeypatch, caplog, auth):
    monkeypatch.delenv("SHABTI_LOGGING_ENABLED", raising=False)
    monkeypatch.setenv("SHABTI_SECURITY_ENABLED", auth)
    caplog.set_level(logging.INFO, logger="shabti")
    monkeypatch.setattr(
        shabti_logging, "get_token_info", token_returning({"sub": "user-1"})
    )
    log_user_action_as(actor, "CREATE COLLECTION", "Create collection basalt")
    await log_user_action("a-token", "QUERY", "User ran a prompt")
    await log_user_action(None, "QUERY", "User ran a prompt")
    assert caplog.records == []


async def test_a_disabled_log_never_decodes_the_token(monkeypatch):
    monkeypatch.delenv("SHABTI_LOGGING_ENABLED", raising=False)
    decoded = []

    async def get_token_info(token):
        decoded.append(token)
        return {"sub": "user-1"}

    monkeypatch.setattr(shabti_logging, "get_token_info", get_token_info)
    await log_user_action("a-token", "QUERY", "User ran a prompt")
    assert decoded == []


# which collection shape reaches the log


class FakeAdminClient:
    def __init__(self, resource):
        self._resource = resource

    async def a_get_client_id(self, client_id):
        return "client-1"

    async def a_get_client_authz_resource(self, client_id, resource_id):
        return self._resource


async def test_an_authz_collection_is_logged_with_its_location_and_owner(
    monkeypatch, caplog, logging_on, auth_on
):
    caplog.set_level(logging.INFO, logger="shabti")
    monkeypatch.setattr(
        document_collections,
        "get_keycloak_admin_client",
        lambda: FakeAdminClient(
            {
                "displayName": "basalt",
                "_id": collection_id,
                "type": "collection:private",
                "attributes": {"shabti_owner": ["user-1"]},
            }
        ),
    )
    monkeypatch.setattr(document_collections, "get_username", lambda _: "Ada Lovelace")
    info = await document_collections.get_collection_info(collection_id)
    assert isinstance(info, AuthzCollectionInfo)
    log_user_action_as(
        actor, "DELETE COLLECTION", "Delete collection", collection=info.model_dump()
    )
    logged = one_entry(caplog).collection
    assert logged["location"] == "private"
    assert logged["owner"] == {"username": "Ada Lovelace", "user_id": "user-1"}


async def test_a_collection_without_authz_is_logged_without_location_or_owner(
    monkeypatch, caplog, logging_on, auth_off
):
    caplog.set_level(logging.INFO, logger="shabti")

    async def get_opensearch_collection_info(_):
        return {"collection_name": "basalt"}

    monkeypatch.setattr(
        document_collections,
        "get_opensearch_collection_info",
        get_opensearch_collection_info,
    )
    info = await document_collections.get_collection_info(collection_id)
    assert type(info) is CollectionInfo
    await log_user_action(
        None, "DELETE COLLECTION", "Delete collection", collection=info.model_dump()
    )
    logged = one_entry(caplog).collection
    assert logged == {"collection_name": "basalt", "collection_id": collection_id}


# the file on disk


def test_the_log_file_lives_in_the_configured_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("SHABTI_LOG_DIR", str(tmp_path))
    assert logging_config()["handlers"]["shabti"]["filename"] == os.path.join(
        str(tmp_path), "shabti_log.json"
    )


def test_the_log_file_falls_back_to_the_working_directory(monkeypatch):
    monkeypatch.delenv("SHABTI_LOG_DIR", raising=False)
    assert logging_config()["handlers"]["shabti"]["filename"] == "./shabti_log.json"


# `log_file` comes from tests/conftest.py, and turns off the propagation `caplog` reads for the
# rest of the module: this has to stay the last test in the file
def test_an_entry_is_written_to_the_file_as_one_json_object(log_file):
    log_user_action_as(
        actor,
        "INSERT DOCUMENT",
        "Ingest document with ID doc-1",
        collection=CollectionInfo(
            collection_name="basalt", collection_id=collection_id
        ).model_dump(),
    )
    logging.getLogger("shabti").handlers[0].flush()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["message"] == "Ingest document with ID doc-1"
    assert entry["severity"] == "INFO"
    assert entry["log_source"] == "shabti"
    assert entry["eventtime"]
    # every extra lands as a top level key, which is what makes the log queryable
    assert entry["action"] == "INSERT DOCUMENT"
    assert entry["user"] == {"name": "Ada Lovelace", "user_id": "user-1"}
    assert entry["collection"]["collection_name"] == "basalt"
