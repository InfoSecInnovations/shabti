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
    ProviderAuthError,
    ProviderKeyInfo,
    ProviderKeyInvalidError,
    UserKeysUnavailableError,
)
from ..authorization import has_access, user_permissions
from ..dependencies.valid_access_token import valid_access_token
from ..functionality.providers.credentials import (
    delete_user_key,
    list_user_keys,
    set_user_key,
)
from ..functionality.providers.discovery import fetch_models
from ..functionality.providers.keys import accepts_user_keys, user_keys_enabled
from ..functionality.providers.registry import get_provider
from ..shabti_logging import log_user_action

router = APIRouter()

opensearch = Depends(RequiredServices(Service.OPENSEARCH))


async def user_keys_allowed(credentials: Annotated[str, Depends(valid_access_token)]):
    if not user_keys_enabled():
        raise UserKeysUnavailableError()
    # the keys are for prompting, so a user who can't do anything in Shabti has no use for one
    if not await has_access(credentials):
        raise UserKeysUnavailableError(
            message="You need access to Shabti to add API keys"
        )


user_keys = Depends(user_keys_allowed)


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
    dependencies=[Depends(RequiredServices(Service.OPENSEARCH, Service.EMBEDDINGS))],
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
    return await user_permissions(credentials)


# a user's own provider keys. only the last four characters of a key are ever sent back
@router.get("/providers/credentials", dependencies=[user_keys])
async def get_provider_credentials_route(
    credentials: Annotated[str, Depends(valid_access_token)],
) -> dict[str, str]:
    return await list_user_keys(credentials)


@router.put("/providers/{provider_id}/credential", dependencies=[user_keys])
async def set_provider_credential_route(
    provider_id: str,
    key_info: ProviderKeyInfo,
    credentials: Annotated[str, Depends(valid_access_token)],
) -> dict[str, str]:
    provider = get_provider(provider_id)
    if not accepts_user_keys(provider):
        raise UserKeysUnavailableError(
            message=f"{provider.name} doesn't take personal API keys", status=400
        )
    key = key_info.key.strip()
    if not key:
        raise ProviderKeyInvalidError(provider.id, message="The key is empty")
    # tried before it's stored, so a mistyped key is caught here rather than at the next prompt
    try:
        await fetch_models(provider, key)
    except ProviderAuthError:
        raise ProviderKeyInvalidError(provider.id) from None
    await set_user_key(credentials, provider.id, key)
    await log_user_action(
        credentials,
        "SET PROVIDER KEY",
        f"Set their API key for {provider.id}",
        provider=provider.id,
    )
    return await list_user_keys(credentials)


# the provider needn't still exist, so a key left behind by one that was removed can be cleared
@router.delete("/providers/{provider_id}/credential", dependencies=[user_keys])
async def delete_provider_credential_route(
    provider_id: str,
    credentials: Annotated[str, Depends(valid_access_token)],
) -> dict[str, str]:
    await delete_user_key(credentials, provider_id)
    await log_user_action(
        credentials,
        "DELETE PROVIDER KEY",
        f"Removed their API key for {provider_id}",
        provider=provider_id,
    )
    return await list_user_keys(credentials)
