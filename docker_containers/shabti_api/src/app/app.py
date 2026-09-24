from fastapi import FastAPI, Request, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from shabti_util import auth_enabled
from .routers import insecure_routes
from .routers import secure_routes
import os
from keycloak import KeycloakPostError, KeycloakAuthenticationError
import json
from shabti_types import ShabtiError, DocumentList, Service
import logging
from fastapi import Depends
from typing import Annotated
from .functionality.document_collections import get_documents, require_collection
from .dependencies.valid_access_token import valid_access_token
from fastapi import UploadFile
from .functionality.document_collections import (
    delete_document,
    get_document_types,
)
from shabti_types import (
    DeletedDocumentInfo,
    ServiceStatus,
    PromptInfo,
    TaskInfo,
    PromptConfigInfo,
    TempFileInfo,
    ModelInfo,
    ModelLoadInfo,
    DocumentIngestInfo,
    DocumentIngestError,
    PromptChunk,
    IngestInfo,
    IngestItemInfo,
)
from .functionality.insert_uploaded_files import insert_uploaded_files
from .functionality.insert_urls import insert_urls
from .functionality.ingest_registry import IngestRegistry, IngestTask, stream_ingest
from .functionality.save_uploads import save_uploads, discard
from .functionality.user_settings import user_id
from .shabti_logging import get_actor
from .functionality.embeddings import (
    context_limit,
    get_embeddings_model_id,
    get_vector_dimension,
)
from .functionality.status import check_llm, check_opensearch, check_tika
from .functionality.opensearch import close_client, sweep_ingesting_documents
from .functionality.run_prompt import run_prompt
from .functionality.load_prompter_config import load_prompter_config
from .functionality.upload_prompt_file import upload_prompt_file
from .functionality.opensearch_binary import serve_binary
from .authorization import (
    UnauthorizedOperationError,
    list_scopes,
)
from .functionality.models import load_model, get_models
from .functionality.chat_model_selection import (
    chat_model_selection,
    set_chat_model_selection,
)
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager, suppress
import asyncio
from uuid import uuid4
from .dependencies.auth_checker import AuthChecker
from .dependencies.no_auth import NoAuth
from .dependencies.prompt_info_validator import PromptInfoValidator
from .dependencies.prompt_body_auth_checker import PromptBodyAuthChecker
from .dependencies.url_list_validator import UrlListValidator
from .dependencies.required_services import RequiredServices


@asynccontextmanager
async def lifespan(app: FastAPI):
    # measuring the model's output size loads it, and creating the first collection would
    # otherwise pay for that. not being able to reach the model at boot is not a reason to refuse
    # to start
    with suppress(Exception):
        model_id = await asyncio.to_thread(get_embeddings_model_id)
        await asyncio.to_thread(get_vector_dimension)
        # after the probe, which is what has the model loaded: its trained context is only reported
        # for a running model, and reading it now is what makes the check on the configured chunk
        # size live for the first ingest rather than the second
        await asyncio.to_thread(context_limit, model_id)
    # documents an ingest was killed part way through: hidden from every listing by the flag they
    # still carry, so nothing else can ever reach them to clean them up. not being able to sweep is
    # no more a reason to refuse to start than the model above
    try:
        swept = await sweep_ingesting_documents()
        if swept:
            logging.getLogger("shabti").info(
                "removed %d unfinished document(s) left by a previous run", len(swept)
            )
    except Exception:
        logging.getLogger("shabti").exception("could not sweep unfinished documents")
    yield
    # before the client closes: the ingests winding down are rolling documents back through it, and
    # closing it under them would strand exactly the partial documents they are there to remove
    await app.state.ingest_registry.shutdown()
    # the OpenSearch client holds an aiohttp session, which can only be closed from the loop it was
    # created on, and this is the only place that loop is still running
    await close_client()


def create_app():
    auth_is_enabled = auth_enabled()

    def none_access_token():
        return None

    # if security is disabled we will just use None as the value of the token, which is supported by the underlying functions
    # this allows us to avoid duplicating all the routes
    access_token = valid_access_token if auth_is_enabled else none_access_token

    # the NoAuth class always returns true so it can be used as the authorization function when security is disabled
    auth_class = AuthChecker if auth_is_enabled else NoAuth

    app = FastAPI(
        lifespan=lifespan,
        swagger_ui_init_oauth={
            "clientId": os.getenv("KEYCLOAK_CLIENT_ID"),
            "clientSecret": os.getenv("KEYCLOAK_CLIENT_SECRET"),
        },
    )

    # on the app rather than in a module global, following the OpenSearch client: an ingest holds
    # asyncio objects bound to the loop it started on, and the tests build an app per session
    registry = IngestRegistry()
    app.state.ingest_registry = registry

    async def ingest_owner(credentials: str | None) -> str:
        # a fixed owner with security disabled, so listing and re-attaching still work there
        return await user_id(credentials) if auth_is_enabled else "local"

    class OwnedIngest:
        """Resolve the caller's own ingest, before the route starts streaming.

        A dependency rather than the route's first statement because two of these routes stream and
        a 404 cannot be reported once the body has begun. `auth_class` is no use here either:
        AuthChecker takes a bare `collection_id`, which FastAPI binds from the path - and with no
        `{collection_id}` in these paths it would silently become a required query parameter.

        Ownership is the whole check, and deliberately so. Re-authorizing the collection on every
        attach was tried and reverted: it is a Keycloak round trip per attach on top of the one the
        POST already paid, it made `test_can_ingest_document[testshared-...]` fail reproducibly with
        a dropped connection to Keycloak, and the web UI is about to poll this listing once a
        second. What it would have bought is narrow - an ingest is the caller's own, listing the
        filenames they submitted moments ago, so losing collection access midway through does not
        make their own upload a secret from them.
        """

        async def __call__(
            self,
            ingest_id: str,
            credentials: Annotated[str, Depends(access_token)],
        ) -> IngestTask:
            return registry.get(ingest_id, await ingest_owner(credentials))

    owned_ingest = OwnedIngest()

    # TODO: probably don't wildcard this
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # there are still a few routes which are specific to security being enabled or not
    if not auth_is_enabled:
        app.include_router(insecure_routes.router)
    else:

        @app.exception_handler(KeycloakPostError)
        def keycloak_post_error_handler(request: Request, exc: KeycloakPostError):
            return JSONResponse(
                content=json.loads(exc.response_body), status_code=exc.response_code
            )

        @app.exception_handler(KeycloakAuthenticationError)
        def keycloak_authentication_error_handler(
            request: Request, exc: KeycloakAuthenticationError
        ):
            return JSONResponse(
                content=json.loads(exc.response_body), status_code=exc.response_code
            )

        app.include_router(secure_routes.router)

    @app.get("/")
    def is_online():
        return Response("Shabti API is up and running!")

    opensearch = Depends(RequiredServices(Service.OPENSEARCH))
    llm = Depends(RequiredServices(Service.LLM))

    @app.get(
        "/collections/{collection_id}/documents",
        response_model_exclude_unset=True,
        dependencies=[opensearch],
    )
    async def get_documents_route(
        collection_id: str,
        credentials: Annotated[str | None, Depends(access_token)],
        search: str | None = None,
        sort: str | None = None,
        max_results: int | None = None,
        filter_document_type: Annotated[list[str] | None, Query()] = None,
        page: int = 0,
    ) -> DocumentList:
        return await get_documents(
            credentials,
            collection_id,
            search,
            sort,
            max_results,
            filter_document_type,
            page,
        )

    @app.get(
        "/collections/{collection_id}/document_types",
        response_model_exclude_unset=True,
        dependencies=[opensearch],
    )
    async def get_document_types_route(
        collection_id: str, credentials: Annotated[str, Depends(access_token)]
    ) -> list[str]:
        return await get_document_types(credentials, collection_id)

    @app.post(
        "/collections/{collection_id}/documents/files",
        response_model_exclude_unset=True,
        status_code=201,
        dependencies=[
            Depends(RequiredServices(Service.OPENSEARCH, Service.LLM, Service.TIKA)),
            Depends(auth_class("update")),
        ],
    )
    async def insert_files_document_route(
        collection_id: str,
        files: list[UploadFile],
        credentials: Annotated[str, Depends(access_token)],
    ) -> IngestInfo:
        await require_collection(collection_id)
        actor = await get_actor(credentials)
        # written to disk before this returns: Starlette closes an upload's spooled temp file when
        # the request ends, and the ingest that reads them now outlives the request
        saved = await save_uploads(files)
        items = [
            IngestItemInfo(item_id=entry.item_id, label=entry.label) for entry in saved
        ]
        try:
            return await registry.start(
                await ingest_owner(credentials),
                collection_id,
                items,
                lambda pool: insert_uploaded_files(actor, collection_id, saved, pool),
            )
        except Exception:
            # an ingest that was never created never gets the chance to clean up after itself, and
            # nothing else knows about these files yet. one that was created does its own cleanup,
            # queued or running, so this is only the narrow window before that
            for entry in saved:
                await discard(entry.item_id)
            raise

    @app.post(
        "/collections/{collection_id}/documents/urls",
        response_model_exclude_unset=True,
        status_code=201,
        dependencies=[
            Depends(RequiredServices(Service.OPENSEARCH, Service.LLM)),
            Depends(auth_class("update")),
            Depends(UrlListValidator()),
        ],
    )
    async def insert_urls_document_route(
        collection_id: str,
        urls: list[str],
        credentials: Annotated[str, Depends(access_token)],
        # 1 ingests only the given URLs; deeper crawls stay within the directory each URL is in, and
        # are clamped to SHABTI_CRAWL_MAX_DEPTH by the loader
        max_depth: Annotated[int, Query(ge=1)] = 1,
    ) -> IngestInfo:
        await require_collection(collection_id)
        actor = await get_actor(credentials)
        items = [IngestItemInfo(item_id=uuid4().hex, label=url) for url in urls]
        return await registry.start(
            await ingest_owner(credentials),
            collection_id,
            items,
            lambda pool: insert_urls(actor, collection_id, items, max_depth, pool),
        )

    @app.get("/ingests", response_model_exclude_unset=True)
    async def get_ingests_route(
        credentials: Annotated[str, Depends(access_token)],
    ) -> list[IngestInfo]:
        return registry.list(await ingest_owner(credentials))

    @app.get("/ingests/{ingest_id}", response_model_exclude_unset=True)
    async def stream_ingest_route(
        task: Annotated[IngestTask, Depends(owned_ingest)],
    ) -> AsyncIterable[DocumentIngestInfo | DocumentIngestError]:
        async for x in stream_ingest(task):
            yield x

    @app.delete("/ingests/{ingest_id}", response_model_exclude_unset=True)
    async def cancel_ingest_route(
        task: Annotated[IngestTask, Depends(owned_ingest)],
    ) -> IngestInfo:
        return await registry.cancel(task)

    @app.delete(
        "/collections/{collection_id}/documents/{document_id}",
        response_model_exclude_unset=True,
        dependencies=[opensearch],
    )
    async def delete_document_route(
        collection_id: str,
        document_id: str,
        credentials: Annotated[str, Depends(access_token)],
    ) -> DeletedDocumentInfo:
        return await delete_document(credentials, collection_id, document_id)

    @app.get("/collections/{collection_id}/scopes")
    async def get_collection_scopes(
        collection_id: str, credentials: Annotated[str, Depends(access_token)]
    ):
        return await list_scopes(credentials, collection_id)

    @app.get(
        "/tasks",
        response_model_exclude_unset=True,
        dependencies=[Depends(access_token)],
    )
    def get_tasks_route() -> dict[str, TaskInfo]:
        tasks = load_prompter_config("tasks")
        return {key: TaskInfo(**value) for key, value in tasks.items()}

    @app.get("/personas", dependencies=[Depends(access_token)])
    def get_personas_route() -> dict[str, PromptConfigInfo]:
        personas = load_prompter_config("personas")
        return {key: PromptConfigInfo(**value) for key, value in personas.items()}

    @app.get("/enhancers", dependencies=[Depends(access_token)])
    def get_enhancers_route() -> dict[str, PromptConfigInfo]:
        enhancers = load_prompter_config("enhancers")
        return {key: PromptConfigInfo(**value) for key, value in enhancers.items()}

    @app.post("/prompt/source_file", dependencies=[opensearch, Depends(access_token)])
    async def prompt_file_route(file: UploadFile) -> TempFileInfo:
        # TODO: should there be more restrictions on this route to avoid spamming the server with files?
        # TODO: maybe something like S3 upload where we pregenerate the URL or ID so a file can only be linked to a prompt?
        return await upload_prompt_file(file)

    body_checker = PromptBodyAuthChecker if auth_is_enabled else NoAuth

    @app.post(
        "/prompt",
        dependencies=[
            Depends(RequiredServices(Service.OPENSEARCH, Service.LLM)),
            Depends(body_checker("read")),
            Depends(PromptInfoValidator()),
        ],
    )
    async def prompt_route(
        prompt_info: PromptInfo, credentials: Annotated[str, Depends(access_token)]
    ) -> AsyncIterable[PromptChunk]:
        async for x in run_prompt(credentials, prompt_info):
            yield x

    @app.get("/status/llm")
    async def llm_status():
        return ServiceStatus(running=await check_llm())

    @app.get("/status/opensearch")
    async def opensearch_status():
        return ServiceStatus(running=await check_opensearch())

    @app.get("/status/tika")
    async def tika_status():
        return ServiceStatus(running=await check_tika())

    @app.get(
        "/files/{collection_id}/{doc_id}",
        dependencies=[opensearch, Depends(auth_class("read"))],
    )
    async def get_files_route(
        collection_id: str,
        doc_id: str,
        credentials: Annotated[str, Depends(access_token)],
    ):
        return await serve_binary(collection_id, doc_id)

    @app.get("/models", dependencies=[llm, Depends(access_token)])
    async def get_models_route(tags: Annotated[list[str] | None, Query()] = None):
        return await get_models(tags)

    # the chat model a client should start on: the user's last choice when security is enabled,
    # otherwise whichever model is currently loaded
    @app.get("/models/chat/selection", dependencies=[llm])
    async def get_chat_model_selection_route(
        credentials: Annotated[str, Depends(access_token)],
    ) -> ModelInfo | None:
        model_id = await chat_model_selection(credentials)
        return ModelInfo(model_name=model_id) if model_id else None

    @app.put("/models/chat/selection")
    async def set_chat_model_selection_route(
        model_info: ModelInfo,
        credentials: Annotated[str, Depends(access_token)],
    ) -> ModelInfo:
        await set_chat_model_selection(credentials, model_info.model_name)
        return model_info

    @app.post("/models/pull", dependencies=[llm, Depends(access_token)])
    async def load_model_route(model_info: ModelInfo) -> AsyncIterable[ModelLoadInfo]:
        # TODO: should this be locked behind higher permissions levels?
        async for x in load_model(model_info.model_name):
            yield x

    @app.exception_handler(UnauthorizedOperationError)
    def unauthorized_operation_handler(
        request: Request, exc: UnauthorizedOperationError
    ):
        # a denial from Keycloak normally arrives as a 403 KeycloakPostError, so this only fires
        # where the decision came back negative rather than as an error. without it the exception
        # is unhandled, which on a streaming route cannot be reported to the caller at all
        return JSONResponse(
            content={"error_type": exc.__class__.__name__},
            status_code=403,
        )

    @app.exception_handler(ShabtiError)
    def shabti_error_handler(request: Request, exc: ShabtiError):
        logger = logging.getLogger("shabti")
        logger.info(
            exc.message,
            extra={
                "action": "HTTP ERROR",
                "error_type": exc.__class__.__name__,
                **{key: value for key, value in vars(exc).items() if key != "message"},
            },
        )
        return JSONResponse(
            content={
                "error_type": exc.__class__.__name__,
                **vars(exc),
            },
            status_code=exc.status,
        )

    return app


app = create_app()
