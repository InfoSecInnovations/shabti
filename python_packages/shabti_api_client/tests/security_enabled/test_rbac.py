import os
import pytest
from shabti_api_client import ShabtiAuthenticationError
import secrets
from .lib import get_admin_client

collection_lookup = {}

filename = "test_doc.txt"
file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)


@pytest.mark.parametrize(
    "shabti_user_client,role,username",
    [
        ("testadmin", "collection_admin", "testadmin"),
        ("testshared", "shared_read_write", "testshared"),
        ("testprivate", "private_collection", "testprivate"),
        ("testsharedread", "shared_read", "testsharedread"),
        ("testnothing", None, "testnothing"),
    ],
    indirect=["shabti_user_client"],
)
async def test_user_info(shabti_user_client, role, username):
    info = await shabti_user_client.get_user_info()
    assert info["preferred_username"] == username
    if not role:
        assert "resource_access" not in info
    else:
        assert role in info["resource_access"]["shabti-auth"]["roles"]


all_permissions = [
    "collection:shared:create",
    "collection:private:assign",
    "delete",
    "collection:private:create",
    "update",
    "read",
]


@pytest.mark.parametrize(
    "shabti_user_client,permissions,shabti_collection_id",
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
    ],
    indirect=["shabti_user_client", "shabti_collection_id"],
)
async def test_user_permissions(
    shabti_user_client, permissions, shabti_collection_id
):  # we need a collection to exist otherwise we don't find all the permissions
    user_permissions = await shabti_user_client.get_permissions()
    for permission in all_permissions:
        if permission in permissions:
            assert permission in user_permissions
        else:
            assert permission not in user_permissions


@pytest.mark.parametrize(
    "shabti_user_client,location",
    [
        ("testadmin", "private"),
        ("testadmin", "shared"),
        ("testshared", "shared"),
        ("testprivate", "private"),
    ],
    indirect=["shabti_user_client"],
)
async def test_can_create_collection(shabti_user_client, location):
    collection_name = secrets.token_hex(8)
    collection_id = await shabti_user_client.create_collection(
        collection_name, location
    )
    assert collection_id
    admin_client = await get_admin_client()
    collections = await admin_client.get_collections()
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == collection_id
            and collection_info.collection_name == collection_name
        ),
        None,
    )
    await admin_client.delete_collection(collection_id)  # clean up collection after use


@pytest.mark.parametrize(
    "shabti_user_client,location",
    [
        ("testshared", "private"),
        ("testprivate", "shared"),
        ("testsharedread", "private"),
        ("testsharedread", "shared"),
        ("testnothing", "private"),
        ("testnothing", "shared"),
    ],
    indirect=["shabti_user_client"],
)
async def test_cannot_create_collection(shabti_user_client, location):
    with pytest.raises(ShabtiAuthenticationError):
        collection_name = secrets.token_hex(8)
        await shabti_user_client.create_collection(collection_name, location)
    admin_client = await get_admin_client()
    collections = await admin_client.get_collections()
    assert not any(
        collection_info.collection_name == collection_name
        for collection_info in collections
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_read_collection(shabti_user_client, shabti_collection_id):
    docs = await shabti_user_client.get_documents(shabti_collection_id)
    assert docs
    collections = await shabti_user_client.get_collections()
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == shabti_collection_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_read_collection(shabti_user_client, shabti_collection_id):
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.get_documents(shabti_collection_id)
    collections = await shabti_user_client.get_collections()
    assert not any(
        collection_info.collection_id == shabti_collection_id
        for collection_info in collections
    )


all_scopes = ["read", "update", "delete"]


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id,scopes",
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
    indirect=["shabti_user_client", "shabti_collection_id"],
)
async def test_collection_scopes(shabti_user_client, shabti_collection_id, scopes):
    user_scopes = await shabti_user_client.get_collection_scopes(shabti_collection_id)
    for scope in all_scopes:
        if scope in scopes:
            assert scope in user_scopes
        else:
            assert scope not in user_scopes


async def ingest_document(shabti_user_client, shabti_collection_id):
    document_id = None
    async for info in shabti_user_client.insert_files(
        shabti_collection_id, [file_path]
    ):
        document_id = info.document_id
    return document_id


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_ingest_document(shabti_user_client, shabti_collection_id):
    document_id = await ingest_document(shabti_user_client, shabti_collection_id)
    assert document_id
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert next(
        (
            doc
            for doc in docs.documents
            if doc.filename == filename and doc.document_id == document_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_ingest_document(shabti_user_client, shabti_collection_id):
    with pytest.raises(ShabtiAuthenticationError):
        await ingest_document(shabti_user_client, shabti_collection_id)
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert not any(doc.filename == filename for doc in docs.documents)


url = "https://www.scrapethissite.com/pages/simple/"


async def ingest_url(shabti_user_client, shabti_collection_id):
    document_id = None
    async for info in shabti_user_client.insert_urls(shabti_collection_id, [url]):
        document_id = info.document_id
    return document_id


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_ingest_url(shabti_user_client, shabti_collection_id):
    document_id = await ingest_url(shabti_user_client, shabti_collection_id)
    assert document_id
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert next(
        (
            doc
            for doc in docs.documents
            if doc.source == url and doc.document_id == document_id
        ),
        None,
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_ingest_url(shabti_user_client, shabti_collection_id):
    with pytest.raises(ShabtiAuthenticationError):
        await ingest_url(shabti_user_client, shabti_collection_id)
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert not any(doc.source == url for doc in docs.documents)


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def can_delete_document(
    shabti_user_client, shabti_collection_id, shabti_document_id
):
    await shabti_user_client.delete_document(shabti_collection_id, shabti_document_id)
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert not any(doc.document_id == shabti_document_id for doc in docs.documents)


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_delete_document(
    shabti_user_client, shabti_collection_id, shabti_document_id
):
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.delete_document(
            shabti_collection_id, shabti_document_id
        )
    admin_client = await get_admin_client()
    docs = await admin_client.get_documents(shabti_collection_id)
    assert next(
        (doc for doc in docs.documents if doc.document_id == shabti_document_id), None
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testshared", "location": "shared"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_delete_collection(shabti_user_client, shabti_collection_id):
    await shabti_user_client.delete_collection(shabti_collection_id)
    admin_client = await get_admin_client()
    collections = await admin_client.get_collections()
    assert not any(
        collection.collection_id == shabti_collection_id for collection in collections
    )


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_delete_collection(shabti_user_client, shabti_collection_id):
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.delete_collection(shabti_collection_id)
    admin_client = await get_admin_client()
    collections = await admin_client.get_collections()
    assert next(
        (
            collection_info
            for collection_info in collections
            if collection_info.collection_id == shabti_collection_id
        ),
        None,
    )


# we can only really check that prompting doesn't error
@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testadmin", {"username": "testadmin", "location": "shared"}),
        ("testadmin", {"username": "testadmin", "location": "private"}),
        ("testadmin", {"username": "testprivate", "location": "private"}),
        ("testsharedread", {"username": "testadmin", "location": "shared"}),
        ("testshared", {"username": "testadmin", "location": "shared"}),
        ("testprivate", {"username": "testprivate", "location": "private"}),
    ],
    indirect=True,
)
async def test_can_prompt(
    shabti_user_client,
    shabti_collection_id,
    shabti_prompt_document_id,
    loaded_chat_model,
):
    async for response in shabti_user_client.prompt(
        shabti_collection_id,
        "What does the word prompting mean?",
        "question",
    ):
        pass


@pytest.mark.parametrize(
    "shabti_user_client,shabti_collection_id",
    [
        ("testsharedread", {"username": "testadmin", "location": "private"}),
        ("testshared", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "private"}),
        ("testprivate", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "shared"}),
        ("testnothing", {"username": "testadmin", "location": "private"}),
    ],
    indirect=True,
)
async def test_cannot_prompt(
    shabti_user_client,
    shabti_collection_id,
    shabti_prompt_document_id,
    loaded_chat_model,
):
    with pytest.raises(ShabtiAuthenticationError):
        async for response in shabti_user_client.prompt(
            shabti_collection_id,
            "What does the word prompting mean?",
            "question",
        ):
            pass


# their own keys for a provider. `stub-byok` takes only users' keys, see testing/stack.ts. no
# collection is needed: a role is access before there's a collection for it to apply to
@pytest.mark.parametrize(
    "shabti_user_client",
    ["testadmin", "testshared", "testprivate", "testsharedread"],
    indirect=True,
)
async def test_can_add_api_key(shabti_user_client):
    try:
        await shabti_user_client.set_api_key("stub-byok", "sk-test-1234")
        assert await shabti_user_client.get_api_keys() == {"stub-byok": "1234"}
    finally:
        assert await shabti_user_client.delete_api_key("stub-byok") == {}


@pytest.mark.parametrize("shabti_user_client", ["testnothing"], indirect=True)
async def test_cannot_add_api_key(shabti_user_client):
    # a user who can't do anything in Shabti has no use for a key
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.set_api_key("stub-byok", "sk-test-1234")
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.get_api_keys()
    with pytest.raises(ShabtiAuthenticationError):
        await shabti_user_client.delete_api_key("stub-byok")
