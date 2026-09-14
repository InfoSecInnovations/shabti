from typing import Literal
from ..authorization import (
    authorize,
    create_resource,
    list_resources,
    list_scopes,
    delete_resource,
    UnauthorizedOperationError,
)
from shabti_util import auth_enabled
from shabti_keycloak import get_token_info, get_keycloak_admin_client, get_username
from uuid import uuid4
from .opensearch import (
    create_collection_index,
    delete_collection_indices,
    create_index_mapping,
    delete_index_mapping,
    get_collection_mappings,
    get_collection_mapping,
    get_opensearch_documents,
    delete_opensearch_document,
    get_opensearch_collection_info,
    collection_index_exists,
    get_document,
    get_opensearch_document_types,
    get_document_file_path,
    freeze_collection_and_get_file_paths,
)
from keycloak import KeycloakPostError
from shabti_types import (
    CollectionInfo,
    AuthzCollectionInfo,
    DocumentInfo,
    DeletedDocumentInfo,
    UserInfo,
    CollectionExistsError,
    CollectionNotFoundError,
    InvalidLocationError,
    InvalidUserError,
    DocumentList,
)
from ..shabti_logging import log_user_action, logging_enabled
import aiofiles.os
import os

type Location = Literal["private", "shared"]


async def get_collections(token) -> list[CollectionInfo] | list[AuthzCollectionInfo]:
    if auth_enabled():
        available_resources = await list_resources(token)
        return [
            AuthzCollectionInfo(
                collection_name=resource["displayName"],
                collection_id=resource["_id"],
                location=resource["type"].replace("collection:", ""),
                owner=UserInfo(
                    user_id=resource["attributes"]["shabti_owner"][0],
                    username=get_username(resource["attributes"]["shabti_owner"][0]),
                ),
            )
            for resource in available_resources
            if (
                resource["type"] == "collection:shared"
                or resource["type"] == "collection:private"
            )
            and not resource["_id"].startswith(
                "default-"
            )  # the default resources are just there to show permissions if no collections are present
        ]
    else:
        # if not using authz we get the collection info from OpenSearch
        return [
            CollectionInfo(
                collection_name=mapping["collection_name"],
                collection_id=mapping["collection_id"],
            )
            for mapping in await get_collection_mappings()
        ]


async def get_collection_info(collection_id) -> CollectionInfo | AuthzCollectionInfo:
    if auth_enabled():
        admin_client = get_keycloak_admin_client()
        client_id = await admin_client.a_get_client_id("shabti-auth")
        resource = await admin_client.a_get_client_authz_resource(
            client_id=client_id, resource_id=collection_id
        )
        return AuthzCollectionInfo(
            collection_name=resource["displayName"],
            collection_id=resource["_id"],
            location=resource["type"].replace("collection:", ""),
            owner=UserInfo(
                user_id=resource["attributes"]["shabti_owner"][0],
                username=get_username(resource["attributes"]["shabti_owner"][0]),
            ),
        )
    opensearch_info = await get_opensearch_collection_info(collection_id)
    return CollectionInfo(
        collection_id=collection_id,
        collection_name=opensearch_info["collection_name"],
    )


# in an unsecured instance collections don't have a location, so None is valid in that case
async def create_collection(
    token,
    display_name: str,
    location: Location | None = None,
    collection_owner: str | None = None,
) -> CollectionInfo | AuthzCollectionInfo:
    print(f"creating {location or ''} collection {display_name}")
    if auth_enabled():
        # when using authz collections should always have a location
        if not location:
            raise InvalidLocationError()
        authorized = await authorize(token, f"collection:{location}:create")
        if not authorized:
            raise UnauthorizedOperationError()
        if collection_owner:
            # only users with the "assign" permission can assign collections to other users
            authorized = await authorize(token, f"collection:{location}:assign")
            if not authorized:
                raise UnauthorizedOperationError()
            admin_client = get_keycloak_admin_client()
            owner_id = await admin_client.a_get_user_id(collection_owner)
            if owner_id is None:
                raise InvalidUserError(
                    f"no user with username {collection_owner} found"
                )
        else:
            token_info = await get_token_info(token)
            owner_id = token_info["sub"]
        try:
            resource_id = await create_resource(
                display_name, f"collection:{location}", owner_id
            )
        except KeycloakPostError as e:
            if e.response_code == 409:
                raise CollectionExistsError(
                    display_name,
                    f"a {location} collection with the name {display_name} already exists",
                )
            else:
                raise e
    else:
        existing = await get_collection_mapping(display_name)
        if existing:
            raise CollectionExistsError(
                display_name,
                f"a collection with the name {display_name} already exists",
            )
        resource_id = str(uuid4())
        # if we don't have authz configured, we write the collection name mapping to OpenSearch
        await create_index_mapping(resource_id, display_name)
    await create_collection_index(resource_id)
    print(f"created {location or ''} collection {display_name} with ID {resource_id}")
    # the branch stays because it decides the return type, but it only builds the value and the
    # message now: both shapes log through the same call
    if auth_enabled():
        info = AuthzCollectionInfo(
            collection_name=display_name,
            collection_id=resource_id,
            location=location,
            owner=UserInfo(user_id=owner_id, username=get_username(owner_id)),
        )
        message = f"Create {location} collection {display_name}"
    else:
        info = CollectionInfo(collection_name=display_name, collection_id=resource_id)
        message = f"Create collection {display_name}"
    await log_user_action(
        token, "CREATE COLLECTION", message, collection=info.model_dump()
    )
    return info


async def delete_collection(token, collection_id):
    print(f"deleting collection with ID {collection_id}")
    info = await get_collection_info(collection_id)
    if auth_enabled():
        authorized = await authorize(token, collection_id, "delete")
        if not authorized:
            raise UnauthorizedOperationError()
        await delete_resource(collection_id)
    else:
        await delete_index_mapping(collection_id)
    file_paths = await freeze_collection_and_get_file_paths(collection_id)
    await delete_collection_indices(collection_id)
    for file_path in file_paths:
        await aiofiles.os.remove(os.path.join(os.getenv("SHABTI_FILES_DIR"), file_path))
    print(f"deleted collection with ID {collection_id}")
    await log_user_action(
        token,
        "DELETE COLLECTION",
        f"Delete collection with ID {collection_id}",
        collection=info.model_dump(),
    )
    return info


def create_document_info(doc):
    return DocumentInfo(
        source=doc["source"],
        ingest_date=doc["ingest_date"],
        vector_count=doc["vector_count"],
        document_id=doc["id"],
        page_count=doc["page_count"],
        media_type=doc["media_type"],
        filename=doc["filename"] if "filename" in doc else None,
        languages=[lang for lang in doc["languages"] if isinstance(lang, str)],
    )


async def get_document_info(collection_id, document_id):
    return create_document_info(await get_document(collection_id, document_id))


async def get_documents(
    token,
    collection_id,
    search: str | None = None,
    sort: str | None = None,
    max_results: int = 0,
    filter_document_type: list[str] | None = None,
    page: int = 0,
):
    if auth_enabled():
        authorized = await authorize(token, collection_id, "read")
        if not authorized:
            raise UnauthorizedOperationError()
    result = await get_opensearch_documents(
        collection_id, search, sort, max_results, filter_document_type, page
    )
    return DocumentList(
        documents=[create_document_info(doc) for doc in result["documents"]],
        total_hits=result["total_hits"],
        total_documents=result["total_documents"],
    )


async def get_document_types(token, collection_id):
    if auth_enabled():
        authorized = await authorize(token, collection_id, "read")
        if not authorized:
            raise UnauthorizedOperationError()
    result = await get_opensearch_document_types(collection_id)
    return result


async def delete_document(token, collection_id, document_id):
    if auth_enabled():
        authorized = await authorize(token, collection_id, "update")
        if not authorized:
            raise UnauthorizedOperationError()
    binary_path = await get_document_file_path(collection_id, document_id)
    doc_info = None
    if logging_enabled():
        doc_info = await get_document_info(collection_id, document_id)
    info = DeletedDocumentInfo(
        collection_id=collection_id,
        document_id=document_id,
        deleted_element_count=await delete_opensearch_document(
            collection_id, document_id
        ),
    )
    if binary_path:
        try:  # if ingesting was cut short by a crash or stopping Shabti manually, the file may not actually be saved
            await aiofiles.os.remove(
                os.path.join(os.getenv("SHABTI_FILES_DIR"), binary_path)
            )
        except FileNotFoundError:
            pass
    # still guarded, even though the logging function checks too: arguments are evaluated eagerly,
    # so doc_info is None here when logging is off and the collection lookup is a Keycloak or
    # OpenSearch round trip an unlogged delete shouldn't pay for
    if logging_enabled():
        await log_user_action(
            token,
            "DELETE DOCUMENT",
            f"Deleted document with ID {document_id} from collection with ID {collection_id}",
            collection=(await get_collection_info(collection_id)).model_dump(),
            document={
                **doc_info.model_dump(),
                "deleted_element_count": info.deleted_element_count,
            },
        )
    return info


async def get_collection_scopes(token, collection_id):
    if not auth_enabled():
        return {"read", "update", "delete"}
    return set(await list_scopes(token, collection_id))


async def require_collection(collection_id: str) -> None:
    """Raise a clean 404 for an ingest into a collection that isn't there.

    Nothing validated this before: with security disabled the failure surfaced from OpenSearch part
    way through the response stream, and an ingest is detached now, so it would land on a task
    nobody is reading rather than on the caller.
    """
    if not await collection_index_exists(collection_id):
        raise CollectionNotFoundError(
            collection_id=collection_id,
            message=f"No collection with ID {collection_id}",
        )
