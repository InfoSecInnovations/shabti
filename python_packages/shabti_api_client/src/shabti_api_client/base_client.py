from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from shabti_types import (
    TaskInfo,
    PromptConfigInfo,
    ModelLoadInfo,
    WebFile,
    PromptChunk,
    UnsupportedFileError,
    DocumentIngestInfo,
    DocumentIngestError,
    DocumentList,
    IngestInfo,
)
import httpx
import json
from typing import Any


def ingest_line(
    json_obj: dict,
) -> DocumentIngestInfo | DocumentIngestError | UnsupportedFileError:
    """One line of an ingest stream as the object a caller sees.

    Discriminated the way the API discriminates it: an `error` key means the item failed. An
    unsupported file keeps arriving as the exception type, which is what the `isinstance` checks in
    the UIs are written against, and every other failure is a plain object so one bad document
    doesn't end the stream for the rest of them.
    """
    if "error" in json_obj:
        if json_obj["error"] == "UnsupportedFileError":
            return UnsupportedFileError(
                json_obj.get("filename"), json_obj.get("message", "")
            )
        return DocumentIngestError(**json_obj)
    return DocumentIngestInfo(**json_obj)


# httpx and uvicorn both expire an idle keep-alive connection after five seconds, so with the two
# timers on the same number the pool can hand back a connection the API is closing as we write to
# it, and the request dies with RemoteProtocolError. Retiring ours first is what removes the race.
# Three seconds keeps a connection warm across the web UI's two second startup poll - otherwise
# that's a handshake per poll, a TLS one with security enabled - while leaving margin for the two
# timers not starting at the same instant. max_connections and max_keepalive_connections are
# httpx's own defaults restated, because Limits() defaults them to None, which means unbounded
# rather than "leave them alone"
POOL_LIMITS = httpx.Limits(
    max_connections=100, max_keepalive_connections=20, keepalive_expiry=3.0
)

# only GET and HEAD, because from out here a connection that died before the response is
# indistinguishable from one the server processed before it went. A repeated POST would start a
# second ingest, and one carrying files= would resend handles the first attempt has already read to
# the end. DELETE and PUT are out for the same reason: a retry landing after the first one succeeded
# turns it into a 404, which is a worse failure than the transport error it replaced
RETRYABLE_METHODS = frozenset({"GET", "HEAD"})


async def send_with_retry(
    method: str, send: Callable[[], Awaitable[httpx.Response]]
) -> httpx.Response:
    """`send` once more if the connection it picked turned out to be one the server was closing.

    Belt and braces for POOL_LIMITS, which narrows that window rather than closing it, and a cold
    stack polling every two seconds against an API busy loading models is where it shows up.
    """
    try:
        return await send()
    except httpx.RemoteProtocolError:
        if method.upper() not in RETRYABLE_METHODS:
            raise
        # the dead connection is dropped from the pool on the way out, so this attempt opens a new
        # one. Once only: with POOL_LIMITS in place, twice in a row is a real fault
        return await send()


class BaseShabtiClient(ABC):
    @abstractmethod
    async def _make_request(
        self,
        method,
        url,
        json=None,
        files: httpx._types.RequestFiles = None,
        params: dict = None,
    ):
        pass

    @abstractmethod
    async def _stream_request(
        self,
        method,
        url,
        json=None,
        files: httpx._types.RequestFiles = None,
        params: dict = None,
    ):
        pass

    async def api_status(self):
        try:
            response = await self._make_request("GET", "/")
            return response.status_code == 200
        except Exception:
            return False

    async def delete_collection(self, collection_id: str) -> str:
        response = await self._make_request("DELETE", f"collections/{collection_id}")
        return response.json()["collection_id"]

    async def start_files_ingest(
        self, collection_id: str, file_paths: list[str]
    ) -> IngestInfo:
        # the handles close once the request has been sent rather than whenever the garbage
        # collector gets around to them, which is what the old inline `open()` calls relied on
        with ExitStack() as stack:
            response = await self._make_request(
                "POST",
                f"/collections/{collection_id}/documents/files",
                files=[
                    ("files", stack.enter_context(open(file_path, "rb")))
                    for file_path in file_paths
                ],
            )
        return IngestInfo(**response.json())

    async def start_urls_ingest(
        self, collection_id: str, urls: list[str], max_depth: int = 1
    ) -> IngestInfo:
        response = await self._make_request(
            "POST",
            f"/collections/{collection_id}/documents/urls",
            json=urls,
            params={"max_depth": max_depth},
        )
        return IngestInfo(**response.json())

    async def get_ingests(self) -> list[IngestInfo]:
        response = await self._make_request("GET", "ingests")
        return [IngestInfo(**item) for item in response.json()]

    async def stream_ingest(self, ingest_id: str):
        async for line in self._stream_request("GET", f"ingests/{ingest_id}"):
            yield ingest_line(json.loads(line))

    async def cancel_ingest(self, ingest_id: str) -> IngestInfo:
        response = await self._make_request("DELETE", f"ingests/{ingest_id}")
        return IngestInfo(**response.json())

    # an ingest outlives the request that starts it now, so this is the POST plus a drain of the
    # stream it returns, which ends when the ingest does. Still a generator that does nothing until
    # it's iterated, so the POST, and with it a permission denial, lands on the first `__anext__`
    async def insert_files(self, collection_id: str, file_paths: list[str]):
        ingest = await self.start_files_ingest(collection_id, file_paths)
        async for item in self.stream_ingest(ingest.ingest_id):
            yield item

    async def insert_urls(
        self, collection_id: str, urls: list[str], max_depth: int = 1
    ):
        ingest = await self.start_urls_ingest(collection_id, urls, max_depth)
        async for item in self.stream_ingest(ingest.ingest_id):
            yield item

    async def get_documents(
        self,
        collection_id: str,
        search: str | None = None,
        sort: str | None = None,
        max_results: int | None = None,
        filter_document_type: list[str] | None = None,
        page: int = 0,
    ):
        params = {}
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort
        if max_results:
            params["max_results"] = max_results
        if filter_document_type:
            params["filter_document_type"] = filter_document_type
        if page:
            params["page"] = page
        response = await self._make_request(
            "GET", f"collections/{collection_id}/documents", params=params
        )
        return DocumentList(**response.json())

    async def get_document_types(self, collection_id: str) -> list[str]:
        response = await self._make_request(
            "GET", f"collections/{collection_id}/document_types"
        )
        return response.json()

    async def delete_document(self, collection_id, document_id) -> str:
        response = await self._make_request(
            "DELETE",
            f"collections/{collection_id}/documents/{document_id}",
        )
        return response.json()["document_id"]

    async def get_tasks(self):
        response = await self._make_request("GET", "/tasks")
        return {key: TaskInfo(**value) for key, value in response.json().items()}

    async def get_personas(self):
        response = await self._make_request("GET", "/personas")
        return {
            key: PromptConfigInfo(**value) for key, value in response.json().items()
        }

    async def get_enhancers(self):
        response = await self._make_request("GET", "/enhancers")
        return {
            key: PromptConfigInfo(**value) for key, value in response.json().items()
        }

    async def prompt(
        self,
        collection_id: str,
        prompt: str,
        task: str,
        persona: str | None = None,
        enhancers: list[str] | None = None,
        file_path: str | None = None,
    ):
        file_id = None
        if file_path:
            response = await self._make_request(
                "POST", "/prompt/source_file", files=[("file", open(file_path, "rb"))]
            )
            file_id = response["id"]
        async for line in self._stream_request(
            "POST",
            "prompt",
            json={
                "collection_id": collection_id,
                "user_input": prompt,
                "task": task,
                "persona": persona,
                "enhancers": enhancers,
                "file_id": file_id,
            },
        ):
            yield PromptChunk(**json.loads(line))

    async def llm_status(self) -> bool:
        response = await self._make_request("GET", "status/llm")
        return response.json()["running"]

    async def opensearch_status(self) -> bool:
        response = await self._make_request("GET", "status/opensearch")
        return response.json()["running"]

    async def tika_status(self) -> bool:
        response = await self._make_request("GET", "status/tika")
        return response.json()["running"]

    # the chat model to start on: the user's last choice when security is enabled, otherwise
    # whichever model is currently loaded
    async def get_chat_model_selection(self) -> str | None:
        response = (await self._make_request("GET", "models/chat/selection")).json()
        return response["model_name"] if response else None

    async def set_chat_model_selection(self, model_name: str):
        await self._make_request(
            "PUT", "models/chat/selection", json={"model_name": model_name}
        )

    async def load_model(self, model_name: str):
        async for line in self._stream_request(
            "POST",
            "/models/pull",
            json={"model_name": model_name},
        ):
            yield ModelLoadInfo(**json.loads(line))

    async def get_file(self, collection_id: str, doc_id: str):
        response = await self._make_request("GET", f"/files/{collection_id}/{doc_id}")
        media_type = response.headers.get("content-type")
        content_disposition = response.headers.get("content-disposition")
        return WebFile(
            bytes=await response.aread(),
            media_type=media_type,
            content_disposition=content_disposition,
        )

    async def get_models(self, tags: list[str] | None = None) -> dict[str, Any]:
        params = {}
        if tags:
            params["tags"] = tags
        return (await self._make_request("GET", "models", params=params)).json()
