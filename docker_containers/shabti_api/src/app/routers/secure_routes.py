from fastapi import APIRouter, Depends, Request
from ..dependencies.required_services import RequiredServices
from ..functionality.document_collections import (
    get_collections,
    create_collection,
    delete_collection,
)
from typing import Annotated
from shabti_keycloak import get_token_info
from shabti_types import (
    Service,
    AuthzCollectionCreateInfo,
    AuthzCollectionInfo,
)
from ..authorization import (
    list_permissions,
    has_scope,
)
import asyncio
from ..dependencies.valid_access_token import valid_access_token

router = APIRouter()

opensearch = Depends(RequiredServices(Service.OPENSEARCH))


@router.get(
    "/collections", response_model_exclude_unset=True, dependencies=[opensearch]
)
async def get_collections_route(
    credentials: Annotated[str, Depends(valid_access_token)],
) -> list[AuthzCollectionInfo]:
    return await get_collections(credentials)


@router.post(
    "/collections",
    response_model_exclude_unset=True,
    status_code=201,
    dependencies=[Depends(RequiredServices(Service.OPENSEARCH, Service.LLM))],
)
async def create_collection_route(
    collection_info: AuthzCollectionCreateInfo,
    credentials: Annotated[str, Depends(valid_access_token)],
) -> AuthzCollectionInfo:
    return await create_collection(
        credentials,
        collection_info.collection_name,
        collection_info.location,
        collection_info.owner_username,
    )


@router.delete(
    "/collections/{collection_id}",
    response_model_exclude_unset=True,
    dependencies=[opensearch],
)
async def delete_collection_route(
    collection_id: str,
    request: Request,
    credentials: Annotated[str, Depends(valid_access_token)],
) -> AuthzCollectionInfo:
    # an ingest is detached now, so without this it would keep writing into an index that has been
    # dropped out from under it
    await request.app.state.ingest_registry.cancel_for_collection(collection_id)
    return await delete_collection(credentials, collection_id)


@router.get("/user_info")
async def get_user_info_route(credentials: Annotated[str, Depends(valid_access_token)]):
    return await get_token_info(credentials)


@router.get("/permissions")
async def get_permissions(credentials: Annotated[str, Depends(valid_access_token)]):
    permissions, read, update, delete = await asyncio.gather(
        list_permissions(credentials),
        has_scope(credentials, "read"),
        has_scope(credentials, "update"),
        has_scope(credentials, "delete"),
    )
    if read:
        permissions.add("read")
    if update:
        permissions.add("update")
    if delete:
        permissions.add("delete")
    return permissions
