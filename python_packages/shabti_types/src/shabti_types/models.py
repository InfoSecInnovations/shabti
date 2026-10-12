from pydantic import BaseModel
from typing import Optional
from enum import StrEnum


class UserInfo(BaseModel):
    username: str
    user_id: str


class BaseCollectionCreateInfo(BaseModel):
    collection_name: str


class AuthzCollectionCreateInfo(BaseCollectionCreateInfo):
    location: str
    owner_username: Optional[str] = None


class CollectionInfo(BaseModel):
    collection_name: Optional[str] = None
    collection_id: str


class AuthzCollectionInfo(CollectionInfo):
    location: str
    owner: UserInfo


class DocumentInfo(BaseModel):
    source: str
    ingest_date: int
    filename: Optional[str] = None
    media_type: str
    document_id: str
    page_count: int
    vector_count: int
    languages: list[str]


class DocumentList(BaseModel):
    documents: list[DocumentInfo]
    total_hits: int
    total_documents: int


class DeletedDocumentInfo(BaseModel):
    collection_id: str
    document_id: str
    deleted_element_count: int


class DocumentIngestInfo(BaseModel):
    progress: int
    total: int
    document_id: str
    document_type: str
    label: str
    # the last event for a document, sent once it is refreshed into the index. left unset on
    # progress events, so `response_model_exclude_unset` keeps it off the lines clients already
    # parse: build it conditionally rather than passing False
    complete: bool = False


class DocumentIngestError(BaseModel):
    error: str
    message: str
    filename: Optional[str] = None
    # which item of an ingest this error belongs to. `filename` is overloaded for that today, and
    # carries a URL's source for the URL route
    label: Optional[str] = None


class IngestStatus(StrEnum):
    # accepted but not started. the caps are on how many ingests run at once rather than on how many
    # may be submitted, so a POST is never refused and waits its turn here instead
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestItemInfo(BaseModel):
    """One file or URL of an ingest.

    Keyed by an `item_id` minted when the ingest is requested rather than by `document_id`: a file
    that fails to load, an unsupported file or a forbidden URL never gets one, and progress can't be
    attributed before the parent document exists. `document_id` lives inside `info` once it appears.
    """

    item_id: str
    label: str
    info: Optional[DocumentIngestInfo] = None
    error: Optional[DocumentIngestError] = None
    # how many files an archive was expanded into. An archive has no document of its own - its
    # members each became an item - so it has neither `info` nor `error`, and without this it reads
    # as queued for ever. Not on the progress stream: that carries only DocumentIngestInfo, whose
    # `document_id` is a required str precisely because an item with no document stays off it
    expanded: Optional[int] = None


class IngestInfo(BaseModel):
    ingest_id: str
    collection_id: str
    status: IngestStatus
    started: int
    finished: Optional[int] = None
    error: Optional[str] = None
    items: list[IngestItemInfo]


class ModelLoadInfo(BaseModel):
    progress: int | float
    total: int | float
    model_name: str
    info: Optional[str] = None


class ModelInfo(BaseModel):
    model_name: str


class ProviderInfo(BaseModel):
    """A source of models: llama.cpp as `local`, or an OpenAI-compatible API the admin added."""

    id: str
    name: str
    credential_mode: str
    has_shared_key: bool


class ProviderModelInfo(BaseModel):
    # qualified as `<provider_id>/<model_id>`, which is what a prompt or a selection names
    id: str
    provider_id: str
    model_id: str
    display_name: str
    category: str
    context_length: Optional[int] = None
    # whether llama.cpp has it loaded. only `local` models have one, a remote model is always ready
    status: Optional[str] = None


class PromptInfo(BaseModel):
    collection_id: str
    task: str
    user_input: str
    persona: Optional[str] = None
    enhancers: Optional[list[str]] = None
    file_id: Optional[str] = None
    # the chat model to answer with, or else the requester's selection
    model: Optional[str] = None


class PageInfo(BaseModel):
    page_number: Optional[int] = None
    source: Optional[str] = None


class PromptSource(BaseModel):
    document_metadata: DocumentInfo
    page_metadata: PageInfo
    # the chunk exactly as stored and as handed to the LLM, rather than the page it came from
    text: str


class PromptChunk(BaseModel):
    response: Optional[str] = None
    source: Optional[PromptSource] = None
    # the model's reasoning, streamed ahead of the response by models that think before answering
    thinking: Optional[str] = None
    # a failure once the response has started streaming, when there is no status code left to use
    error: Optional[str] = None


class ServiceStatus(BaseModel):
    running: bool


class Service(StrEnum):
    """A service the API needs something from, as named in a ServiceUnavailableError."""

    OPENSEARCH = "opensearch"
    LLM = "llm"
    TIKA = "tika"
    KEYCLOAK = "keycloak"

    @property
    def label(self) -> str:
        return SERVICE_LABELS[self]


SERVICE_LABELS = {
    Service.OPENSEARCH: "OpenSearch",
    Service.LLM: "LLM",
    Service.TIKA: "Tika",
    Service.KEYCLOAK: "Keycloak",
}


class PromptConfigInfo(BaseModel):
    prompt: Optional[str] = None


class TaskInfo(PromptConfigInfo):
    greeting: str


class TempFileInfo(BaseModel):
    id: str


class WebFile(BaseModel):
    bytes: bytes
    media_type: str
    content_disposition: str
