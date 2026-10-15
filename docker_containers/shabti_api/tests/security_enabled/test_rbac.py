# This test should be run once Shabti is installed with the demo RBAC configuration
# For best results it should be fresh installation with no collections created or tweaks made to the access controls
# Do not use this on a production instance!

from shabti_keycloak import get_keycloak_client, get_keycloak_admin_openid_token
from ...src.app.authorization import UnauthorizedOperationError
from ...src.app.functionality.document_collections import (
    delete_collection,
    get_documents,
    delete_document,
    get_collections,
)
import pytest
from keycloak import KeycloakPostError, KeycloakAuthenticationError
from .lib import create_collection_for_user, delete_collection_as_admin
import os
import json
import secrets


@pytest.mark.parametrize(
    "user,role",
    [
        ("testadmin", "collection_admin"),
        ("testshared", "shared_read_write"),
        ("testprivate", "private_collection"),
        ("testsharedread", "shared_read"),
        ("testnothing", None),
    ],
)
async def test_user_info(user, role, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.get(
        "/user_info",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["preferred_username"] == user
    if not role:
        assert "resource_access" not in json_response
    else:
        assert role in json_response["resource_access"]["shabti-auth"]["roles"]


all_permissions = [
    "collection:shared:create",
    "collection:private:assign",
    "delete",
    "collection:private:create",
    "update",
    "read",
]


@pytest.mark.parametrize(
    "user,permissions,shabti_collection_id",
    [
        (
            "testadmin",
            [
                "collection:shared:create",
                "collection:private:assign",
                "delete",
                "collection:private:create",
                "update",
                "read",
            ],
            {"username": "testadmin", "location": "shared"},
        ),
        (
            "testshared",
            ["collection:shared:create", "delete", "update", "read"],
            {"username": "testshared", "location": "shared"},
        ),
        (
            "testprivate",
            ["collection:private:create", "delete", "update", "read"],
            {"username": "testprivate", "location": "private"},
        ),
        ("testsharedread", ["read"], {"username": "testadmin", "location": "shared"}),
        ("testnothing", [], {"username": "testadmin", "location": "shared"}),
        ("testnothing", [], {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_user_permissions(
    user, permissions, shabti_collection_id, shabti_client
):  # we need a collection to exist to be able to detect all the permissions
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.get(
        "/permissions",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200
    response_json = response.json()
    for permission in all_permissions:
        if permission in permissions:
            assert permission in response_json
        else:
            assert permission not in response_json


@pytest.mark.parametrize(
    "user,location",
    [
        ("testadmin", "private"),
        ("testadmin", "shared"),
        ("testshared", "shared"),
        ("testprivate", "private"),
    ],
)
async def test_can_create_collection(user, location, shabti_client):
    # test function behind the API call
    backend_collection_name = secrets.token_hex(8)
    backend_collection_id = await create_collection_for_user(
        user, location, backend_collection_name
    )
    assert backend_collection_id

    # test the API call itself
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    collection_name = secrets.token_hex(8)
    response = shabti_client.post(
        "/collections",
        headers={"Authorization": f"Bearer {token['access_token']}"},
        json={"collection_name": collection_name, "location": location},
    )
    assert response.status_code == 201
    response_json = response.json()
    assert response_json["collection_id"]
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == backend_collection_id
            and collection_info.collection_name == backend_collection_name
        ),
        None,
    )
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == response_json["collection_id"]
            and collection_info.collection_name == collection_name
        ),
        None,
    )
    # clean up created collections
    await delete_collection_as_admin(backend_collection_id)
    await delete_collection_as_admin(response_json["collection_id"])


@pytest.mark.parametrize(
    "user,location",
    [
        ("testshared", "private"),
        ("testprivate", "shared"),
        ("testsharedread", "private"),
        ("testsharedread", "shared"),
        ("testnothing", "private"),
        ("testnothing", "shared"),
    ],
)
async def test_cannot_create_collection(user, location, shabti_client):
    # test the API call itself
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    collection_name = secrets.token_hex(8)
    response = shabti_client.post(
        "/collections",
        headers={"Authorization": f"Bearer {token['access_token']}"},
        json={"collection_name": collection_name, "location": location},
    )
    assert response.status_code == 403
    backend_collection_name = secrets.token_hex(8)
    with pytest.raises((KeycloakPostError, KeycloakAuthenticationError)):
        # test function behind the API call
        await create_collection_for_user(user, location, backend_collection_name)
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert not any(
        collection_info.collection_name == collection_name
        or collection_info.collection_name == backend_collection_name
        for collection_info in collections
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_read_collection(user, shabti_collection_id, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    docs = await get_documents(token["access_token"], shabti_collection_id)
    assert docs is not None
    response = shabti_client.get(
        f"/collections/{shabti_collection_id}/documents",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json() is not None
    collections = await get_collections(token["access_token"])
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == shabti_collection_id
        ),
        None,
    )
    response = shabti_client.get(
        "/collections", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert response.status_code == 200
    assert next(
        (
            collection_info
            for collection_info in response.json()
            if collection_info["collection_id"] == shabti_collection_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_read_collection(user, shabti_collection_id, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.get(
        f"/collections/{shabti_collection_id}/documents",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 403
    with pytest.raises(
        (UnauthorizedOperationError, KeycloakPostError, KeycloakAuthenticationError)
    ):
        await get_documents(token["access_token"], shabti_collection_id)
    collections = await get_collections(token["access_token"])
    assert not any(
        collection_info.collection_id == shabti_collection_id
        for collection_info in collections
    )
    response = shabti_client.get(
        "/collections", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert response.status_code == 200
    assert not any(
        collection_info["collection_id"] == shabti_collection_id
        for collection_info in response.json()
    )


all_scopes = ["read", "update", "delete"]


@pytest.mark.parametrize(
    "user,shabti_collection_id, scopes",
    [
        (
            "testadmin",
            {"username": "testadmin", "location": "shared"},
            ["read", "update", "delete"],
        ),
        (
            "testadmin",
            {"username": "testadmin", "location": "private"},
            ["read", "update", "delete"],
        ),
        (
            "testadmin",
            {"username": "testprivate", "location": "private"},
            ["read", "update", "delete"],
        ),
        ("testsharedread", {"username": "testadmin", "location": "shared"}, ["read"]),
        (
            "testshared",
            {"username": "testadmin", "location": "shared"},
            ["read", "update", "delete"],
        ),
        (
            "testprivate",
            {"username": "testprivate", "location": "private"},
            ["read", "update", "delete"],
        ),
        ("testsharedread", {"username": "testadmin", "location": "private"}, []),
        ("testshared", {"username": "testadmin", "location": "private"}, []),
        ("testprivate", {"username": "testadmin", "location": "private"}, []),
        ("testprivate", {"username": "testadmin", "location": "shared"}, []),
        ("testnothing", {"username": "testadmin", "location": "shared"}, []),
        ("testnothing", {"username": "testadmin", "location": "private"}, []),
    ],
    indirect=["shabti_collection_id"],
)
async def test_collection_scopes(user, shabti_collection_id, scopes, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.get(
        f"/collections/{shabti_collection_id}/scopes",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    response_json = response.json()
    for scope in all_scopes:
        if scope in scopes:
            assert scope in response_json
        else:
            assert scope not in response_json


filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


def drain_ingest(shabti_client, ingest_id, headers):
    """Follow a detached ingest to its end, returning the document id it produced.

    The POST only accepts the ingest now, so the wait moved here: the stream ends when the ingest
    does. The caller's own token goes on this request too - reading someone else's ingest is
    refused, which is what the scoping tests below check.
    """
    document_id = None
    with shabti_client.stream(
        "GET", f"/ingests/{ingest_id}", headers=headers
    ) as stream:
        for item in stream.iter_lines():
            if not item.strip():
                continue
            line = json.loads(item)
            if "document_id" in line:
                document_id = line["document_id"]
    return document_id


def ingest_document_api(user, collection_id, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    with open(file_path, "rb") as f:
        response = shabti_client.post(
            f"/collections/{collection_id}/documents/files",
            files=[("files", f)],
            headers=headers,
        )
    if response.status_code != 201:
        return (None, response)
    return (
        drain_ingest(shabti_client, response.json()["ingest_id"], headers),
        response,
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_ingest_document(user, shabti_collection_id, shabti_client):
    result = ingest_document_api(user, shabti_collection_id, shabti_client)
    assert result[1].status_code == 201
    assert result[0]
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert next(
        (
            doc
            for doc in docs.documents
            if doc.filename == filename and doc.document_id == result[0]
        ),
        None,
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_ingest_document(user, shabti_collection_id, shabti_client):
    result = ingest_document_api(user, shabti_collection_id, shabti_client)
    assert not result[0]
    assert result[1].status_code == 403
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert not any(doc.filename == filename for doc in docs.documents)


url = "https://www.scrapethissite.com/pages/simple/"


def ingest_url_api(user, collection_id, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    response = shabti_client.post(
        f"/collections/{collection_id}/documents/urls",
        json=[url],
        headers=headers,
    )
    if response.status_code != 201:
        return (None, response)
    return (
        drain_ingest(shabti_client, response.json()["ingest_id"], headers),
        response,
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_ingest_url(user, shabti_collection_id, shabti_client):
    result = ingest_url_api(user, shabti_collection_id, shabti_client)
    assert result[1].status_code == 201
    assert result[0]
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert next((doc for doc in docs.documents if doc.source == url), None)


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_ingest_url(user, shabti_collection_id, shabti_client):
    result = ingest_url_api(user, shabti_collection_id, shabti_client)
    assert not result[0]
    assert result[1].status_code == 403
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert not any(doc.source == url for doc in docs.documents)


async def delete_document_with_user(user, collection_id, document_id):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    return await delete_document(token["access_token"], collection_id, document_id)


async def delete_document_api_with_user(
    user, collection_id, document_id, shabti_client
):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    return shabti_client.delete(
        f"/collections/{collection_id}/documents/{document_id}",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_delete_document(
    user, shabti_collection_id, shabti_document_id, shabti_client
):
    assert await delete_document_with_user(
        user, shabti_collection_id, shabti_document_id
    )
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert not any(doc.document_id == shabti_document_id for doc in docs.documents)


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_delete_document_api(
    user, shabti_collection_id, shabti_document_id, shabti_client
):
    response = await delete_document_api_with_user(
        user, shabti_collection_id, shabti_document_id, shabti_client
    )
    assert response.status_code == 200
    assert "document_id" in response.json()
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert not any(doc.document_id == shabti_document_id for doc in docs.documents)


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_delete_document(user, shabti_collection_id, shabti_document_id):
    with pytest.raises(
        (UnauthorizedOperationError, KeycloakPostError, KeycloakAuthenticationError)
    ):
        await delete_document_with_user(user, shabti_collection_id, shabti_document_id)
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert next(
        (doc for doc in docs.documents if doc.document_id == shabti_document_id), None
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_delete_document_api(
    user, shabti_collection_id, shabti_document_id, shabti_client
):
    response = await delete_document_api_with_user(
        user, shabti_collection_id, shabti_document_id, shabti_client
    )
    assert response.status_code == 403
    assert "document_id" not in response.json()
    admin_token = get_keycloak_admin_openid_token()
    docs = await get_documents(admin_token["access_token"], shabti_collection_id)
    assert next(
        (doc for doc in docs.documents if doc.document_id == shabti_document_id), None
    )


async def delete_collection_with_user(user, collection_id):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    return await delete_collection(token["access_token"], collection_id)


async def delete_collection_api_with_user(user, collection_id, shabti_client):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    return shabti_client.delete(
        f"/collections/{collection_id}",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testshared", "location": "shared"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_delete_collection(user, shabti_collection_id):
    await delete_collection_with_user(user, shabti_collection_id)
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert not any(
        collection_info.collection_id == shabti_collection_id
        for collection_info in collections
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testshared", "location": "shared"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_delete_collection_api(user, shabti_collection_id, shabti_client):
    response = await delete_collection_api_with_user(
        user, shabti_collection_id, shabti_client
    )
    assert response.status_code == 200
    assert "collection_id" in response.json()
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert not any(
        collection_info.collection_id == shabti_collection_id
        for collection_info in collections
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_delete_collection(user, shabti_collection_id, shabti_client):
    with pytest.raises(
        (UnauthorizedOperationError, KeycloakPostError, KeycloakAuthenticationError)
    ):
        await delete_collection_with_user(user, shabti_collection_id)
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == shabti_collection_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_delete_collection_api(user, shabti_collection_id, shabti_client):
    response = await delete_collection_api_with_user(
        user, shabti_collection_id, shabti_client
    )
    assert response.status_code == 403
    admin_token = get_keycloak_admin_openid_token()
    collections = await get_collections(admin_token["access_token"])
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == shabti_collection_id
        ),
        None,
    )


# we can't really test the output of the prompter but we can check whether the user is able to use a specific collection to prompt or not
@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_can_prompt(
    user,
    shabti_collection_id,
    shabti_client,
    shabti_prompt_document_id,
    loaded_chat_model,
):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "question",
            "user_input": "What does the word prompting mean?",
        },
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "user,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=["shabti_collection_id"],
)
async def test_cannot_prompt(
    user,
    shabti_collection_id,
    shabti_client,
    shabti_prompt_document_id,
    loaded_chat_model,
):
    keycloak_client = get_keycloak_client()
    token = keycloak_client.token(user, "test")
    response = shabti_client.post(
        "/prompt",
        json={
            "collection_id": shabti_collection_id,
            "task": "question",
            "user_input": "What does the word prompting mean?",
        },
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "shabti_collection_id",
    [{"username": "testadmin", "location": "private"}],
    indirect=["shabti_collection_id"],
)
async def test_an_ingest_is_only_visible_to_its_owner(
    shabti_collection_id, shabti_client
):
    keycloak_client = get_keycloak_client()
    owner = keycloak_client.token("testadmin", "test")
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    with open(file_path, "rb") as f:
        response = shabti_client.post(
            f"/collections/{shabti_collection_id}/documents/files",
            files=[("files", f)],
            headers=owner_headers,
        )
    assert response.status_code == 201
    ingest_id = response.json()["ingest_id"]
    other = keycloak_client.token("testnothing", "test")
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    # a foreign ingest is a 404 rather than a 403, so ids can't be probed for existence
    listed = shabti_client.get("/ingests", headers=other_headers)
    assert listed.status_code == 200
    assert not any(item["ingest_id"] == ingest_id for item in listed.json())
    assert (
        shabti_client.get(f"/ingests/{ingest_id}", headers=other_headers).status_code
        == 404
    )
    assert (
        shabti_client.delete(f"/ingests/{ingest_id}", headers=other_headers).status_code
        == 404
    )
    # the owner still sees it, and it still finishes
    assert any(
        item["ingest_id"] == ingest_id
        for item in shabti_client.get("/ingests", headers=owner_headers).json()
    )
    assert drain_ingest(shabti_client, ingest_id, owner_headers)


# their own keys for a provider, see test_provider_keys.py. `stub-byok` takes only users' keys.
# no collection is needed: a role is access before there's a collection for it to apply to
@pytest.mark.parametrize(
    "user", ["testadmin", "testshared", "testprivate", "testsharedread"]
)
async def test_can_add_api_key(user, shabti_client):
    token = get_keycloak_client().token(user, "test")
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    try:
        response = shabti_client.put(
            "/providers/stub-byok/credential",
            json={"key": "sk-test-1234"},
            headers=headers,
        )
        assert response.status_code == 200
        response = shabti_client.get("/providers/credentials", headers=headers)
        assert response.json() == {"stub-byok": "1234"}
    finally:
        response = shabti_client.delete(
            "/providers/stub-byok/credential", headers=headers
        )
        assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/providers/credentials"),
        ("put", "/providers/stub-byok/credential"),
        ("delete", "/providers/stub-byok/credential"),
    ],
)
async def test_cannot_add_api_key(method, url, shabti_client):
    # a user who can't do anything in Shabti has no use for a key
    token = get_keycloak_client().token("testnothing", "test")
    response = shabti_client.request(
        method,
        url,
        json={"key": "sk-test-1234"},
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert response.status_code == 403
    assert response.json()["error_type"] == "UserKeysUnavailableError"
