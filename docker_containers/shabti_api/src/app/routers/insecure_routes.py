from fastapi import APIRouter, Depends, Request
from ..dependencies.required_services import RequiredServices
from ..functionality.document_collections import (
    create_collection,
    get_collections,
    delete_collection,
)
from shabti_types import (
    Service,
    BaseCollectionCreateInfo,
    CollectionInfo,
)

router = APIRouter()

opensearch = Depends(RequiredServices(Service.OPENSEARCH))


@router.post(
    "/collections",
    response_model_exclude_unset=True,
    status_code=201,
    dependencies=[Depends(RequiredServices(Service.OPENSEARCH, Service.LLM))],
)
async def create_collection_route(
    collection_info: BaseCollectionCreateInfo,
) -> CollectionInfo:
    return await create_collection(None, collection_info.collection_name)


@router.get(
    "/collections", response_model_exclude_unset=True, dependencies=[opensearch]
)
async def get_collections_route() -> list[CollectionInfo]:
    return await get_collections(None)


@router.delete(
    "/collections/{collection_id}",
    response_model_exclude_unset=True,
    dependencies=[opensearch],
)
async def delete_collection_route(
    collection_id: str, request: Request
) -> CollectionInfo:
    # an ingest is detached now, so without this it would keep writing into an index that has been
    # dropped out from under it
    await request.app.state.ingest_registry.cancel_for_collection(collection_id)
    return await delete_collection(None, collection_id)
